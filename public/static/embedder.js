// Sentence embeddings in the browser for the repetition check (design file 04).
// The model and the library are served from this website (see scripts/fetch_assets.py),
// so no text leaves the participant's browser except to this study's own server.

let extractorPromise = null;

export function normalise(text) {
  return text.normalize("NFKC").toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();
}

/** Start loading the model in the background. Safe to call repeatedly. */
export function preload(modelName) {
  if (!extractorPromise) {
    extractorPromise = (async () => {
      const base = new URL(".", window.location.href);
      const tf = await import(new URL("static/vendor/transformers.min.js", base).href);
      tf.env.allowRemoteModels = false;
      tf.env.allowLocalModels = true;
      tf.env.localModelPath = new URL("models/", base).href;
      tf.env.backends.onnx.wasm.wasmPaths = new URL("static/vendor/", base).href;
      return await tf.pipeline("feature-extraction", modelName, { dtype: "q8", device: "wasm" });
    })();
    extractorPromise.catch((e) => console.warn("[rgt] embedding model not available:", e));
  }
  return extractorPromise;
}

function timeout(ms) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms));
}

/**
 * Embed the two poles. Returns {similarity: number[], contrast: number[]} or null
 * if the model is not available within waitSeconds (the server then uses text similarity).
 */
export async function embedPoles(simPole, conPole, modelName, waitSeconds) {
  try {
    const extractor = await Promise.race([preload(modelName), timeout(waitSeconds * 1000)]);
    const out = await extractor([normalise(simPole), normalise(conPole)], { pooling: "mean", normalize: true });
    const rows = out.tolist();
    const round = (v) => v.map((x) => Math.round(x * 1e5) / 1e5);
    return { similarity: round(rows[0]), contrast: round(rows[1]) };
  } catch (e) {
    console.warn("[rgt] embedding failed, server will use text similarity:", e);
    return null;
  }
}
