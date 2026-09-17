/* Local visual-quality heuristics, not document recognition or identity verification. */
(() => {
  'use strict';
  const config = Object.freeze({
    maxSide: 640, darkLevel: 28, darkFraction: .78, brightLevel: 248, brightFraction: .82,
    cannyLow: 24, cannyHigh: 65, minContrast: 9, blurVariance: 24,
    minComponent: 55, minArea: .015, ratioMin: 1.15, ratioMax: 2.15,
    borderBand: .1, borderBins: 12, borderSupport: .58,
    minOccupancy: .30, frameMargin: .16, edgeMargin: .015,
    minTexture: .008, maxTexture: .32, textureTiles: 3, interiorInset: .12,
  });
  const messages = Object.freeze({
    dark: 'La imagen est\u00e1 demasiado oscura. Busca un lugar con mejor luz.',
    bright: 'Hay demasiada luz o reflejo sobre el documento. Cambia el \u00e1ngulo y repite.',
    blur: 'La foto est\u00e1 un poco borrosa. Mant\u00e9n el tel\u00e9fono firme y vuelve a intentarlo.',
    missing: 'No distinguimos un documento completo. Ubica toda la c\u00e9dula dentro del marco y repite.',
    small: 'El documento se ve muy peque\u00f1o. Ac\u00e9rcalo un poco, sin perder el enfoque.',
    framing: 'Aseg\u00farate de que las cuatro esquinas est\u00e9n visibles y el documento est\u00e9 centrado.',
    unavailable: 'No pudimos revisar la calidad autom\u00e1ticamente. Repite la foto o confirma que est\u00e1 n\u00edtida y completa antes de usarla.',
  });
  const degraded = () => ({decision: 'review', reasons: ['unavailable'], message: messages.unavailable});
  function regionStats(gray, edges, width, height, box) {
    const x0 = Math.max(1, Math.floor(box.x)), y0 = Math.max(1, Math.floor(box.y));
    const x1 = Math.min(width - 1, Math.ceil(box.x + box.w)), y1 = Math.min(height - 1, Math.ceil(box.y + box.h));
    let n = 0, total = 0, square = 0, dark = 0, bright = 0, lap = 0, lapSquare = 0, edgeCount = 0;
    const tiles = new Uint32Array(8), counts = new Uint32Array(8);
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
      const i = y * width + x, value = gray[i];
      const l = gray[i - 1] + gray[i + 1] + gray[i - width] + gray[i + width] - 4 * value;
      n++; total += value; square += value * value; lap += l; lapSquare += l * l;
      if (value < config.darkLevel) dark++;
      if (value > config.brightLevel) bright++;
      const tile = Math.min(1, Math.floor((y - y0) * 2 / (y1 - y0))) * 4 + Math.min(3, Math.floor((x - x0) * 4 / (x1 - x0)));
      counts[tile]++;
      if (edges[i]) { edgeCount++; tiles[tile]++; }
    }
    if (!n) throw new Error('Empty analysis area');
    return {dark: dark / n, bright: bright / n, contrast: Math.sqrt(Math.max(0, square / n - (total / n) ** 2)),
      blur: lapSquare / n - (lap / n) ** 2, texture: edgeCount / n,
      tiles: Array.from(tiles).filter((v, i) => v / Math.max(1, counts[i]) >= config.minTexture).length};
  }
  function rectangles(edges, width, height) {
    const seen = new Uint8Array(width * height), queue = new Int32Array(width * height), found = [];
    for (let start = 0; start < edges.length; start++) {
      if (!edges[start] || seen[start]) continue;
      let head = 0, tail = 1, left = width, right = 0, top = height, bottom = 0;
      queue[0] = start; seen[start] = 1;
      while (head < tail) {
        const p = queue[head++], x = p % width, y = Math.floor(p / width);
        left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx, ny = y + dy, next = ny * width + nx;
          if (nx < 0 || nx >= width || ny < 0 || ny >= height || seen[next] || !edges[next]) continue;
          seen[next] = 1; queue[tail++] = next;
        }
      }
      const w = right - left, h = bottom - top, ratio = w / h;
      if (tail < config.minComponent || w * h < width * height * config.minArea || ratio < config.ratioMin || ratio > config.ratioMax) continue;
      // Connected edges must support all four sides, not just a text line or an L shape.
      const sides = Array.from({length: 4}, () => new Set());
      for (let k = 0; k < tail; k++) {
        const x = queue[k] % width - left, y = Math.floor(queue[k] / width) - top;
        const bx = Math.min(config.borderBins - 1, Math.floor(x / w * config.borderBins));
        const by = Math.min(config.borderBins - 1, Math.floor(y / h * config.borderBins));
        if (y < h * config.borderBand) sides[0].add(bx);
        if (y > h * (1 - config.borderBand)) sides[1].add(bx);
        if (x < w * config.borderBand) sides[2].add(by);
        if (x > w * (1 - config.borderBand)) sides[3].add(by);
      }
      if (sides.every(s => s.size / config.borderBins >= config.borderSupport)) found.push({x: left, y: top, w, h});
    }
    return found.sort((a, b) => b.w * b.h - a.w * a.h);
  }
  function analyzePixels(image, expected) {
    const {width, height, data} = image;
    const gray = new jsfeat.matrix_t(width, height, jsfeat.U8C1_t);
    const softened = new jsfeat.matrix_t(width, height, jsfeat.U8C1_t);
    const edges = new jsfeat.matrix_t(width, height, jsfeat.U8C1_t);
    jsfeat.imgproc.grayscale(data, width, height, gray);
    jsfeat.imgproc.gaussian_blur(gray, softened, 3, 0);
    jsfeat.imgproc.canny(softened, edges, config.cannyLow, config.cannyHigh);
    const fw = Math.min(width * .82, height * .72 * (85.60 / 53.98));
    const fallback = {x: (width - fw) / 2, y: (height - fw / 1.586) / 2, w: fw, h: fw / 1.586};
    const frame = expected ? {x: expected.x * width, y: expected.y * height, w: expected.w * width, h: expected.h * height} : fallback;
    const stats = regionStats(gray.data, edges.data, width, height, frame);
    const flags = {blur_ok: null, exposure_ok: true, document_present: false, framing_ok: null, glare_ok: null};
    const reject = code => ({decision: 'reject', reasons: [code], message: messages[code], checks: flags});
    if (stats.dark >= config.darkFraction) { flags.exposure_ok = false; return reject('dark'); }
    if (stats.bright >= config.brightFraction) { flags.exposure_ok = false; return reject('bright'); }
    const candidates = rectangles(edges.data.subarray(0, width * height), width, height);
    if (!candidates.length) {
      if (stats.contrast >= config.minContrast && stats.blur < config.blurVariance) { flags.blur_ok = false; return reject('blur'); }
      return reject('missing');
    }
    const box = candidates[0], inset = config.interiorInset;
    const inside = regionStats(gray.data, edges.data, width, height,
      {x: box.x + box.w * inset, y: box.y + box.h * inset, w: box.w * (1 - 2 * inset), h: box.h * (1 - 2 * inset)});
    if (inside.dark >= config.darkFraction || inside.bright >= config.brightFraction) {
      flags.exposure_ok = false; return reject(inside.dark >= config.darkFraction ? 'dark' : 'bright');
    }
    if (inside.contrast < config.minContrast) return reject('missing');
    flags.blur_ok = inside.blur >= config.blurVariance;
    if (!flags.blur_ok) return reject('blur');
    flags.document_present = inside.texture >= config.minTexture && inside.texture <= config.maxTexture && inside.tiles >= config.textureTiles;
    if (!flags.document_present) return reject('missing');
    if (box.w * box.h / (frame.w * frame.h) < config.minOccupancy) { flags.framing_ok = false; return reject('small'); }
    const margin = config.frameMargin;
    flags.framing_ok = box.x > width * config.edgeMargin && box.y > height * config.edgeMargin &&
      box.x + box.w < width * (1 - config.edgeMargin) && box.y + box.h < height * (1 - config.edgeMargin) &&
      Math.abs(box.x + box.w / 2 - frame.x - frame.w / 2) < frame.w * margin &&
      Math.abs(box.y + box.h / 2 - frame.y - frame.h / 2) < frame.h * margin;
    if (!flags.framing_ok) return reject('framing');
    return {decision: 'pass', reasons: [], message: '', checks: flags};
  }
  async function analyze(photo, {expected, aspect} = {}) {
    const start = performance.now(), url = URL.createObjectURL(photo), image = new Image(), canvas = document.createElement('canvas');
    let timer;
    try {
      await new Promise((resolve, reject) => {
        timer = setTimeout(reject, 4000); image.onload = resolve; image.onerror = reject; image.src = url;
      });
      const scale = Math.min(1, config.maxSide / Math.max(image.naturalWidth, image.naturalHeight));
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale)); canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      const ctx = canvas.getContext('2d', {willReadFrequently: true});
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      // Native still photos can have another aspect/FOV: do not pretend an exact crop mapping.
      const aligned = aspect && Math.abs(image.naturalWidth / image.naturalHeight / aspect - 1) < .1;
      await new Promise(resolve => setTimeout(resolve, 0));
      const result = analyzePixels(ctx.getImageData(0, 0, canvas.width, canvas.height), aligned ? expected : null);
      return {...result, elapsedMs: performance.now() - start};
    } catch (_) { return {...degraded(), elapsedMs: performance.now() - start}; }
    finally { clearTimeout(timer); image.onload = image.onerror = null; image.src = ''; URL.revokeObjectURL(url); canvas.width = canvas.height = 0; }
  }
  window.DocumentCaptureQuality = Object.freeze({analyze, config, degraded});
})();
