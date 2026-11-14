"""Steps 3, 4 and 6: preprocessing, clustering and validation."""
import numpy as np
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import RobustScaler


class Preprocessor:
    """RobustScaler (+ optional PCA). Fitted once on the full period and reused
    for the 5-year windows so every window lands in the same feature space."""

    def __init__(self, features, pca_var=0.9):
        self.features, self.pca_var = features, pca_var

    def fit(self, df):
        self.scaler = RobustScaler().fit(df[self.features])
        Z = self.scaler.transform(df[self.features])
        self.pca = PCA(self.pca_var, random_state=0).fit(Z) if self.pca_var else None
        return self

    def transform(self, df):
        Z = self.scaler.transform(df[self.features])
        return self.pca.transform(Z) if self.pca is not None else Z


def _sil(X, labels, seed=0):
    m = labels >= 0
    if len(set(labels[m])) < 2:
        return float("nan")
    return float(silhouette_score(X[m], labels[m], sample_size=min(5000, m.sum()), random_state=seed))


def kmeans_search(X, ks=range(3, 9), seed=0):
    """Elbow (inertia) + silhouette for each k; best k = highest silhouette."""
    rows = []
    for k in ks:
        if k >= len(X):
            break
        km = KMeans(k, n_init=20, random_state=seed).fit(X)
        rows.append({"k": k, "inertia": float(km.inertia_), "silhouette": _sil(X, km.labels_),
                     "davies_bouldin": float(davies_bouldin_score(X, km.labels_)), "model": km})
    best = max(rows, key=lambda r: r["silhouette"])
    return best, [{k: v for k, v in r.items() if k != "model"} for r in rows]


def kmeans_confidence(km, X):
    """1 - d1/d2: 0 = on the border between two regimes, 1 = right at a centre."""
    d = np.sort(km.transform(X), 1)
    return 1 - d[:, 0] / np.maximum(d[:, 1], 1e-9)


def run_hdbscan(X, min_cluster_size=None):
    mcs = min_cluster_size or max(10, int(0.02 * len(X)))
    h = HDBSCAN(min_cluster_size=mcs, min_samples=max(5, mcs // 2), copy=True).fit(X)
    return h.labels_, h.probabilities_


def centroids(X, labels):
    ids = sorted(set(labels) - {-1})
    return ids, np.array([X[labels == i].mean(0) for i in ids])


def nearest(X, ids, cents):
    d = ((X[:, None, :] - cents[None]) ** 2).sum(-1)
    return np.array(ids)[d.argmin(1)]


def cluster(X, method="auto", ks=range(3, 9), seed=0):
    """Returns labels, confidence, outlier flag and a comparison report.

    auto = K-Means gives every cell a regime; HDBSCAN is run alongside and
    its noise points are kept as an "unusual for its regime" flag. If HDBSCAN
    separates the data clearly better (higher silhouette, <20% noise) it is
    used as the primary labelling instead.
    """
    best, k_table = kmeans_search(X, ks, seed)
    km = best["model"]
    h_labels, h_prob = run_hdbscan(X)
    noise = float((h_labels == -1).mean())
    report = {
        "kmeans": {"k": best["k"], "silhouette": best["silhouette"],
                   "davies_bouldin": best["davies_bouldin"], "k_search": k_table},
        "hdbscan": {"n_clusters": int(len(set(h_labels) - {-1})), "noise_share": noise,
                    "silhouette_non_noise": _sil(X, h_labels)},
    }
    use_h = method == "hdbscan" or (
        method == "auto" and report["hdbscan"]["n_clusters"] >= 3 and noise < 0.2
        and report["hdbscan"]["silhouette_non_noise"] > best["silhouette"] + 0.05)
    if use_h:
        ids, cents = centroids(X, h_labels)
        labels = np.where(h_labels >= 0, h_labels, nearest(X, ids, cents))
        conf = np.where(h_labels >= 0, h_prob, 0.0)
        report["chosen"] = "hdbscan"
    else:
        labels, conf = km.labels_.copy(), kmeans_confidence(km, X)
        report["chosen"] = "kmeans"
    report["silhouette"] = _sil(X, labels)
    report["davies_bouldin"] = float(davies_bouldin_score(X, labels))
    return labels, conf, h_labels == -1, report


def seed_stability(X, k, ref, seeds=range(1, 11)):
    """Mean ARI between the chosen labels and K-Means re-runs with other seeds."""
    return float(np.mean([adjusted_rand_score(ref, KMeans(k, n_init=10, random_state=s).fit_predict(X))
                          for s in seeds]))
