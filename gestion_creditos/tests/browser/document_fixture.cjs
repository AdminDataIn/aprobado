// Synthetic geometry only: no real identity, numbers or user photographs.
function drawDocument(canvas, scene = 'sharp') {
  const c = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  c.fillStyle = scene === 'dark' ? '#050505' : scene === 'bright' ? '#ffffff'
    : scene.startsWith('low-contrast') ? '#e7e1ce' : scene === 'dark-background' ? '#202020' : '#657477';
  c.fillRect(0, 0, w, h);
  if (['dark', 'bright', 'wall'].includes(scene)) return;
  if (scene === 'keyboard') {
    c.fillStyle = '#202628';
    for (let row = 0; row < 5; row++) for (let col = 0; col < 12; col++) c.fillRect(w * (.07 + col * .075), h * (.35 + row * .052), w * .057, h * .039);
    return;
  }
  let dw = Math.round(Math.min(w * .75, h * .5 * 1.586));
  if (scene.includes('small')) dw = Math.round(dw * .4);
  const dh = Math.round(dw / 1.586);
  const x = scene === 'outside' || scene === 'cut-left' ? -dw * .15
    : scene === 'cut-right' ? w - dw * .85 : Math.round((w - dw) / 2);
  const y = scene === 'offcenter' ? h * .1 : scene === 'cut-top' ? -dh * .15
    : scene === 'cut-bottom' ? h - dh * .85 : Math.round((h - dh) / 2);
  if (scene === 'alternative') {
    c.fillStyle = '#ece6d3'; c.fillRect(w * .03, h * .02, w * .94, w * .94 / 1.586);
  }
  if (scene === 'ambiguous') {
    const outer = dw * 1.23, tall = outer / 1.586;
    c.fillStyle = '#c2bdb1'; c.fillRect((w - outer) / 2, (h - tall) / 2, outer, tall);
  }
  c.save();
  if (scene.startsWith('rotated')) {
    c.setTransform(0, 1, -1, 0, (w + h) / 2, (h - w) / 2);
  }
  c.fillStyle = scene === 'white-card' ? '#ffffff' : '#ece6d3'; c.fillRect(x, y, dw, dh);
  if (scene === 'textureless') {
    c.fillStyle = '#344d53'; c.fillRect(x + dw * .2, y + dh * .2, dw * .15, dh * .15);
  } else if (!scene.includes('blank')) {
    c.fillStyle = '#227780'; c.fillRect(x + dw * .07, y + dh * .20, dw * .20, dh * .6);
    c.fillStyle = '#344d53';
    for (let row = 0; row < 8; row++) for (let col = 0; col < 18; col++) {
      c.fillRect(x + dw * (.33 + col * .031), y + dh * (.16 + row * .085), dw * (.01 + (col % 3) * .003), dh * .021);
    }
    for (let i = 0; i < 28; i++) c.fillRect(x + dw * (.05 + i * .032), y + dh * .88, dw * .012, dh * .04);
  }
  if (scene === 'worn') {
    c.fillStyle = '#b9b3a0';
    for (let i = 0; i < 8; i++) c.fillRect(x + dw * (.05 + i * .12), y + dh * .05, dw * .025, dh * .04);
    c.strokeStyle = '#c9c3b0'; c.lineWidth = dw * .003;
    for (let i = 0; i < 5; i++) { c.beginPath(); c.moveTo(x + dw * .03, y + dh * (.3 + i * .1)); c.lineTo(x + dw * .9, y + dh * (.25 + i * .1)); c.stroke(); }
  }
  if (scene === 'texture-overload') {
    c.fillStyle = '#151515';
    for (let offset = 0; offset < dw; offset += 18) c.fillRect(x + offset, y, 9, dh);
  }
  if (scene === 'glare') {
    c.fillStyle = '#ffffff'; c.fillRect(x + dw * .40, y + dh * .08, dw * .54, dh * .70);
  }
  if (scene === 'ocr') {
    c.fillStyle = '#ece6d3'; c.fillRect(x + dw * .30, y + dh * .21, dw * .68, dh * .59);
    c.fillStyle = '#142127'; c.font = `bold ${Math.round(dh * .105)}px sans-serif`;
    c.fillText('DOCUMENTO', x + dw * .32, y + dh * .42);
    c.fillText('99000123', x + dw * .32, y + dh * .67);
  }
  c.restore();
  if (scene.includes('blur')) {
    // Downsample then interpolate so the fixture does not depend on Canvas filter support.
    const copy = document.createElement('canvas'); copy.width = Math.round(w / 38); copy.height = Math.round(h / 38);
    copy.getContext('2d').drawImage(canvas, 0, 0, copy.width, copy.height);
    c.drawImage(copy, 0, 0, w, h);
    copy.width = copy.height = 0;
  }
  if (scene === 'dim') {
    c.fillStyle = '#0004'; c.fillRect(0, 0, w, h);
  }
}
module.exports = drawDocument;
