// Synthetic geometry only: no real identity, numbers or user photographs.
function drawDocument(canvas, scene = 'sharp') {
  const c = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  c.fillStyle = scene === 'dark' ? '#050505' : scene === 'bright' ? '#ffffff' : '#657477';
  c.fillRect(0, 0, w, h);
  if (['dark', 'bright', 'wall'].includes(scene)) return;
  if (scene === 'keyboard') {
    c.fillStyle = '#202628';
    for (let row = 0; row < 5; row++) for (let col = 0; col < 12; col++) c.fillRect(w * (.07 + col * .075), h * (.35 + row * .052), w * .057, h * .039);
    return;
  }
  let dw = Math.min(w * .75, h * .5 * 1.586), dh = dw / 1.586;
  if (scene === 'small') { dw *= .4; dh *= .4; }
  const x = scene === 'outside' ? -dw * .15 : (w - dw) / 2;
  const y = scene === 'offcenter' ? h * .1 : (h - dh) / 2;
  c.fillStyle = scene === 'white-card' ? '#ffffff' : '#ece6d3'; c.fillRect(x, y, dw, dh);
  if (scene !== 'blank') {
    c.fillStyle = '#227780'; c.fillRect(x + dw * .07, y + dh * .20, dw * .20, dh * .6);
    c.fillStyle = '#344d53';
    for (let row = 0; row < 8; row++) for (let col = 0; col < 18; col++) {
      c.fillRect(x + dw * (.33 + col * .031), y + dh * (.16 + row * .085), dw * (.01 + (col % 3) * .003), dh * .021);
    }
    for (let i = 0; i < 28; i++) c.fillRect(x + dw * (.05 + i * .032), y + dh * .88, dw * .012, dh * .04);
  }
  if (scene === 'blur') {
    const copy = document.createElement('canvas'); copy.width = w; copy.height = h;
    copy.getContext('2d').drawImage(canvas, 0, 0);
    c.filter = `blur(${Math.max(w, h) / 65}px)`; c.drawImage(copy, 0, 0); c.filter = 'none';
    copy.width = copy.height = 0;
  }
  if (scene === 'dim') {
    c.fillStyle = '#0004'; c.fillRect(0, 0, w, h);
  }
}
module.exports = drawDocument;
