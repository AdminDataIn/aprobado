(() => {
  'use strict';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const storage = {
    get(key) { try { return sessionStorage.getItem(key) || ''; } catch (_) { return ''; } },
    set(key, value) { try { sessionStorage.setItem(key, value); } catch (_) { /* Optional resume only. */ } },
    remove(key) { try { sessionStorage.removeItem(key); } catch (_) { /* Backend remains authoritative. */ } },
  };
  const problem = (message, retryable = false) => Object.assign(new Error(message), {retryable});
  async function requestJSON(url, body, scope) {
    const grantScope = scope.hasAttribute && scope.hasAttribute('data-capture-grant');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const endpoint = new URL(url, location.href);
      if (endpoint.origin !== location.origin) throw problem('No pudimos abrir la captura segura. Recarga la pagina.');
      const options = {credentials: 'same-origin', mode: 'same-origin', cache: 'no-store',
        referrerPolicy: 'same-origin', signal: controller.signal, headers: {Accept: 'application/json'}};
      if (body) {
        const csrf = scope.querySelector('[name=csrfmiddlewaretoken]');
        if (!csrf || !csrf.value) throw problem('Recarga la p\u00e1gina para renovar la sesi\u00f3n e int\u00e9ntalo de nuevo.');
        options.method = 'POST'; options.body = body;
        options.headers['X-CSRFToken'] = csrf.value;
      }
      const response = await fetch(endpoint.href, options);
      if (response.redirected || response.status === 401) throw problem(grantScope
        ? 'No pudimos abrir la captura segura. Vuelve a tu solicitud en el equipo de origen.'
        : 'Tu sesi\u00f3n termin\u00f3. Inicia sesi\u00f3n de nuevo y vuelve a tu solicitud.');
      if (response.status === 403) {
        if (grantScope) {
          let data;
          try { data = await response.json(); } catch (_) { /* CSRF rejection can be HTML. */ }
          const error = problem('No pudimos autorizar la captura. Recarga esta p\u00e1gina y vuelve a intentar.');
          error.grantInvalid = data && data.code === 'CAPTURE_GRANT_INVALID';
          throw error;
        }
        throw problem('No pudimos autorizar la operaci\u00f3n. Recarga la p\u00e1gina e inicia sesi\u00f3n con tu cuenta.');
      }
      if (response.status === 429) throw problem('Has realizado varios intentos. Espera un momento antes de reintentar.', true);
      if (response.status >= 500) throw problem('La captura no est\u00e1 disponible por un momento. Int\u00e9ntalo de nuevo.', true);
      if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
        throw problem('No recibimos la respuesta esperada. Revisa tu sesi\u00f3n y vuelve a intentarlo.');
      }
      let data;
      try { data = await response.json(); } catch (_) { throw problem('No pudimos confirmar la respuesta. Intentalo de nuevo.', true); }
      if (!response.ok) throw Object.assign(problem(grantScope
        ? 'No se pudo completar la captura. Revisa la foto e intenta de nuevo.'
        : 'No se pudo completar la operacion. Comprueba que el enlace siga vigente y que uses la misma cuenta.'),
        {sessionInvalid: !grantScope && [400, 404].includes(response.status)});
      if (!data || typeof data !== 'object') throw problem('No pudimos confirmar la respuesta. Intentalo de nuevo.', true);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw problem('La respuesta tard\u00f3 demasiado. Comprueba tu conexi\u00f3n y reintenta.', true);
      if (error instanceof TypeError) throw problem('No pudimos conectar. Comprueba tu conexi\u00f3n y reintenta.', true);
      throw error;
    } finally { clearTimeout(timer); }
  }

  function initHandoff(panel) {
    if (panel.dataset.captureReady) return;
    panel.dataset.captureReady = 'true';
    const field = name => panel.querySelector(`[data-${name}]`);
    const status = field('capture-status'), hidden = field('sesion-documental');
    const create = field('crear-enlace'), retry = field('reintentar-estado');
    const result = field('handoff-result'), link = field('enlace-captura'), copy = field('copiar-enlace');
    const canvas = field('capture-qr'), expiry = field('capture-expiry');
    const dialog = field('capture-dialog'), frame = field('capture-frame');
    const form = panel.closest('form') || document;
    const context = panel.dataset.contexto || '';
    const base = panel.dataset.crear.replace(/crear\/$/, '');
    const key = `captura:${panel.dataset.producto}:${context}`;
    let timer, expiryTimer, cycle = 0, attempts = 0, failures = 0, busy = false;
    let currentState = '', secureLink = '', ownLink = '';
    const identityFields = ['cedula_frontal', 'cedula_trasera', 'id_documento_identidad_frontal', 'id_documento_identidad_reverso']
      .map(id => document.getElementById(id)).filter(input => input && form.contains(input));
    const initialFields = identityFields.map(input => ({input, required: input.required, existing: input.dataset.existing}));
    const mobile = window.matchMedia('(max-width: 640px), (pointer: coarse) and (max-width: 1024px)');
    function adaptScreen() {
      panel.dataset.mobile = String(mobile.matches);
      link.hidden = !mobile.matches;
      field('capture-title').textContent = mobile.matches ? 'Captura tu c\u00e9dula' : 'Contin\u00faa la captura desde tu celular';
      field('capture-description').textContent = mobile.matches
        ? 'Necesitamos una foto del frente y del reverso.'
        : 'Escanea el QR con tu celular. El enlace solo permite esta captura documental.';
      const account = panel.querySelector('.capture-account');
      if (account) account.hidden = mobile.matches;
      if (!hidden.value) create.textContent = mobile.matches ? 'Tomar fotos de mi c\u00e9dula' : 'Continuar desde mi celular';
    }
    function paint(state, message) { panel.dataset.state = state; status.textContent = message; }
    function clearLink() {
      clearTimeout(expiryTimer);
      secureLink = ''; ownLink = '';
      link.removeAttribute('href');
      result.hidden = true;
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    }
    function clearLocal() {
      storage.remove(key); hidden.value = ''; clearLink();
      initialFields.forEach(({input, required, existing}) => {
        input.required = required;
        if (existing === undefined) delete input.dataset.existing;
        else input.dataset.existing = existing;
      });
    }
    function completeInForm() {
      panel.dispatchEvent(new CustomEvent('captura:finalizada', {bubbles: true}));
    }
    function openOwnCapture() {
      if (!ownLink || !dialog || !frame) return;
      frame.src = ownLink;
      dialog.showModal();
    }
    if (dialog && frame) {
      field('capture-close').addEventListener('click', () => dialog.close());
      dialog.addEventListener('close', () => { frame.src = 'about:blank'; restartPoll(); });
      window.addEventListener('message', async event => {
        if (event.origin !== location.origin || event.source !== frame.contentWindow ||
            event.data?.type !== 'captura-finalizada' || event.data.id !== hidden.value) return;
        try {
          const data = await requestJSON(`${base}${hidden.value}/estado/?solicitud_id=${encodeURIComponent(context)}`, null, form);
          if (data.estado === 'FINALIZADA') { applyState(data.estado); dialog.close(); }
        } catch (error) { paint('error', error.message); }
      });
      link.addEventListener('click', event => { if (ownLink) { event.preventDefault(); openOwnCapture(); } });
    }
    function applyState(state) {
      currentState = state;
      if (['FINALIZADA', 'UTILIZADA', 'EXPIRADA', 'REVOCADA'].includes(state)) {
        clearTimeout(timer); clearLink();
        retry.hidden = true;
        create.hidden = state === 'FINALIZADA';
        if (state !== 'FINALIZADA') clearLocal();
        if (state === 'FINALIZADA' || state === 'UTILIZADA') {
          paint('success', state === 'FINALIZADA' ? 'C\u00e9dula capturada correctamente' : 'Captura ya utilizada. Puedes iniciar una nueva captura.');
          if (state === 'FINALIZADA') {
            ['cedula_frontal', 'cedula_trasera', 'id_documento_identidad_frontal', 'id_documento_identidad_reverso'].forEach(id => {
              const input = document.getElementById(id);
              if (input && form.contains(input)) { input.required = false; input.dataset.existing = 'true'; }
            });
            completeInForm();
          }
        } else {
          paint('expired', state === 'EXPIRADA' ? 'El enlace venci\u00f3. Genera uno nuevo para continuar.' : 'Este enlace fue revocado. Genera uno nuevo para continuar.');
          create.textContent = 'Generar nuevo enlace';
        }
        return false;
      }
      if (!['ABIERTA', 'CANJEADA'].includes(state)) throw problem('No pudimos confirmar el estado. Vuelve a consultarlo.', true);
      paint('waiting', state === 'CANJEADA' ? 'Captura en curso desde tu celular...' : 'Esperando captura desde tu celular...');
      create.hidden = !!secureLink || state === 'CANJEADA';
      if (!secureLink && state === 'ABIERTA') {
        status.textContent = 'Hay una captura pendiente. Genera un nuevo enlace si necesitas abrirla de nuevo.';
        create.textContent = 'Generar nuevo enlace';
      }
      retry.hidden = true;
      return true;
    }
    async function poll(run) {
      if (run !== cycle || !UUID.test(hidden.value)) return;
      if (attempts++ >= 120) {
        paint('paused', 'La consulta autom\u00e1tica se paus\u00f3. Puedes consultar el estado nuevamente.');
        retry.hidden = false; return;
      }
      try {
        const data = await requestJSON(`${base}${hidden.value}/estado/?solicitud_id=${encodeURIComponent(context)}`, null, form);
        if (run !== cycle) return;
        failures = 0;
        if (applyState(data.estado)) timer = setTimeout(() => poll(run), 5000);
      } catch (error) {
        if (run !== cycle) return;
        paint('error', error.message);
        if (error.sessionInvalid) { clearLocal(); currentState = ''; }
        retry.hidden = false;
        if (!secureLink && !error.retryable) {
          currentState = '';
          create.hidden = false;
          create.textContent = 'Generar nuevo enlace';
        }
        // Reads may retry; a creation POST is never replayed automatically.
        if (error.retryable && ++failures < 3) timer = setTimeout(() => poll(run), 5000 * failures);
      }
    }
    function restartPoll() { clearTimeout(timer); attempts = failures = 0; poll(++cycle); }
    function drawQR(url) {
      try {
        const code = window.qrcode(0, 'M');
        code.addData(url, 'Byte'); code.make();
        const count = code.getModuleCount(), cell = 4, quiet = 4;
        canvas.width = canvas.height = (count + quiet * 2) * cell;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#000';
        for (let row = 0; row < count; row++) {
          for (let col = 0; col < count; col++) {
            if (code.isDark(row, col)) ctx.fillRect((col + quiet) * cell, (row + quiet) * cell, cell, cell);
          }
        }
        canvas.hidden = false;
        field('qr-help').textContent = 'Escanea este c\u00f3digo con la c\u00e1mara de tu celular';
      } catch (_) {
        canvas.hidden = true;
        field('qr-help').textContent = 'Puedes continuar copiando el enlace seguro a tu celular.';
      }
    }

    create.addEventListener('click', async () => {
      if (busy) return;
      busy = create.disabled = true;
      panel.setAttribute('aria-busy', 'true');
      clearTimeout(timer);
      const run = ++cycle;
      paint('loading', 'Preparando tu enlace seguro...'); retry.hidden = true;
      try {
        const body = new FormData();
        if (context) body.set('solicitud_id', context);
        // Only an explicit click renews an OPEN session recovered without its token.
        const endpoint = currentState === 'ABIERTA' && UUID.test(hidden.value) && !secureLink
          ? `${base}${hidden.value}/regenerar/` : panel.dataset.crear;
        const data = await requestJSON(endpoint, body, form);
        const url = new URL(data.enlace, location.href);
        const expires = new Date(data.expira_en);
        if (!UUID.test(data.id) || url.origin !== location.origin || !url.hash ||
            url.pathname !== `${base}${data.id}/movil/` || !Number.isFinite(expires.getTime())) {
          throw problem('No pudimos preparar el enlace seguro. Intentalo nuevamente.');
        }
        hidden.value = data.id; storage.set(key, data.id);
        secureLink = url.href; link.href = secureLink;
        if (data.enlace_propio) {
          const own = new URL(data.enlace_propio, location.href);
          if (own.origin !== location.origin || own.pathname !== `${base}${data.id}/` || !own.hash)
            throw problem('No pudimos preparar la captura de tu cuenta.');
          ownLink = own.href;
        }
        drawQR(secureLink);
        expiry.textContent = `Enlace v\u00e1lido hasta ${expires.toLocaleTimeString('es-CO', {hour: '2-digit', minute: '2-digit'})}.`;
        result.hidden = false; create.hidden = true;
        applyState('ABIERTA');
        clearTimeout(expiryTimer);
        const issuedId = data.id;
        expiryTimer = setTimeout(() => {
          if (hidden.value === issuedId) { clearLink(); restartPoll(); }
        }, Math.max(0, expires.getTime() - Date.now()));
        attempts = failures = 0; poll(run);
        if (mobile.matches && ownLink) openOwnCapture();
      } catch (error) {
        paint('error', error instanceof TypeError ? 'No pudimos preparar el enlace. Intentalo nuevamente.' : error.message);
        create.hidden = false; create.textContent = 'Reintentar';
        retry.hidden = !UUID.test(hidden.value);
      } finally { busy = create.disabled = false; panel.setAttribute('aria-busy', 'false'); }
    });
    copy.addEventListener('click', async () => {
      if (!secureLink) return;
      try { await navigator.clipboard.writeText(secureLink); paint('waiting', 'Enlace copiado. Abrelo en tu celular.'); }
      catch (_) { paint('waiting', 'No pudimos copiar el enlace. Escanea el QR desde tu celular.'); }
    });
    retry.addEventListener('click', restartPoll);
    mobile.addEventListener('change', adaptScreen);
    // Listener installation precedes all optional storage access.
    const saved = hidden.value || storage.get(key);
    hidden.value = UUID.test(saved) ? saved : '';
    if (saved && !hidden.value) storage.remove(key);
    adaptScreen();
    if (hidden.value) {
      create.hidden = true; paint('loading', 'Consultando tu captura pendiente...'); restartPoll();
    }
  }
  document.querySelectorAll('[data-capture-handoff]').forEach(initHandoff);

  const mobile = document.querySelector('[data-capture-mobile]');
  if (!mobile || mobile.dataset.captureReady) return;
  mobile.dataset.captureReady = 'true';
  const delegated = mobile.hasAttribute('data-capture-grant');
  let token = location.hash.slice(1);
  if (!delegated) history.replaceState(null, '', location.pathname + location.search);
  const status = mobile.querySelector('[data-capture-status]');
  const find = name => mobile.querySelector(`[data-${name}]`);
  const video = find('camera-video'), preview = find('camera-preview');
  const open = find('canjear'), take = find('tomar-foto'), repeat = find('repetir');
  const accept = find('usar-foto'), finish = find('finalizar');
  const exitCamera = find('salir'), guide = find('camera-guide'), zoom = find('camera-zoom');
  const buttons = [open, take, repeat, accept, finish];
  let stream, blob, previewURL, monitor, cameraTimer, generation = 0;
  let trackSettings = {}, trackCaps = {};
  let quality = null, expectedFrame = null, zoomTimer, zoomPending = false;
  const orientation = window.matchMedia('(orientation: landscape)');
  let resumePortrait = false;
  let step = 'ready', side = 'FRONTAL', busy = false, terminal = false, redeemed = false;
  let checking = false;

  function stopCamera() {
    ++generation;
    clearTimeout(cameraTimer);
    clearTimeout(zoomTimer);
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
    trackSettings = {}; trackCaps = {};
    find('zoom-control').hidden = true;
    guide.hidden = true;
  }
  function clearPhoto() {
    blob = null;
    quality = null; find('confirm-quality').checked = false;
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
    preview.removeAttribute('src');
  }
  function show(next, message = '') {
    step = next; mobile.dataset.state = next;
    status.textContent = message;
    const immersive = ['opening', 'live', 'checking', 'preview'].includes(next);
    mobile.classList.toggle('camera-immersive', immersive);
    exitCamera.hidden = !immersive;
    find('camera-media').hidden = !immersive;
    find('viewfinder').hidden = next !== 'live';
    preview.hidden = !['checking', 'preview'].includes(next);
    find('camera-instructions').hidden = next !== 'ready';
    const example = mobile.querySelector('[data-example-side]');
    example.dataset.exampleSide = side;
    example.setAttribute('aria-label', side === 'FRONTAL' ? 'Ejemplo ilustrativo del frente, sin datos personales' : 'Ejemplo ilustrativo del reverso, sin datos personales');
    open.hidden = next !== 'ready'; take.hidden = next !== 'live';
    repeat.hidden = accept.hidden = next !== 'preview'; finish.hidden = next !== 'finish';
    open.textContent = redeemed ? 'Abrir c\u00e1mara de nuevo' : 'Abrir c\u00e1mara';
    take.disabled = next !== 'live' || video.readyState < 2;
    find('camera-step').textContent = side === 'FRONTAL' ? '1 de 2 \u00b7 Frontal' : '2 de 2 \u00b7 Posterior';
    find('camera-title').textContent = next === 'finish' || next === 'done'
      ? 'Ambas caras recibidas' : side === 'FRONTAL' ? 'Frente del documento' : 'Reverso del documento';
    find('camera-hint').hidden = next !== 'live';
    find('manual-review').hidden = next !== 'preview' || !quality || quality.decision !== 'review';
    accept.disabled = !canUsePhoto();
    find('zoom-control').hidden = next !== 'live' || !zoomRange();
    if (next === 'live') requestAnimationFrame(updateGuide);
  }
  function updateGuide() {
    if (step !== 'live' || !video.videoWidth || !video.videoHeight) { guide.hidden = true; return; }
    // CSS fixes object-fit: contain and object-position: center. Exclude letterboxing.
    const box = video.getBoundingClientRect(), parent = find('viewfinder').getBoundingClientRect();
    const scale = Math.min(box.width / video.videoWidth, box.height / video.videoHeight);
    const width = video.videoWidth * scale, height = video.videoHeight * scale;
    const ratio = 85.60 / 53.98;
    const frameWidth = Math.min(width * .82, height * .72 * ratio);
    const frameHeight = frameWidth / ratio;
    expectedFrame = {x: (width - frameWidth) / (2 * width), y: (height - frameHeight) / (2 * height), w: frameWidth / width, h: frameHeight / height};
    guide.style.width = `${frameWidth}px`; guide.style.height = `${frameHeight}px`;
    guide.style.left = `${box.left - parent.left + (box.width - frameWidth) / 2}px`;
    guide.style.top = `${box.top - parent.top + (box.height - frameHeight) / 2}px`;
    guide.hidden = frameWidth <= 0;
  }
  video.addEventListener('loadedmetadata', updateGuide);
  video.addEventListener('resize', updateGuide);
  window.addEventListener('resize', updateGuide);
  window.addEventListener('orientationchange', () => requestAnimationFrame(updateGuide));
  if (window.visualViewport) window.visualViewport.addEventListener('resize', updateGuide);
  if (window.ResizeObserver) new ResizeObserver(updateGuide).observe(find('viewfinder'));

  function zoomRange() {
    const z = trackCaps.zoom;
    if (!z || !Number.isFinite(z.min) || !Number.isFinite(z.max) || z.min <= 0) return null;
    const step = z.step > 0 ? z.step : .1;
    const max = z.min + Math.floor((Math.min(z.max, 2.5) - z.min) / step + 1e-8) * step;
    return max > z.min ? {...z, step, max} : null;
  }
  function readTrack(track) {
    try { trackSettings = track.getSettings ? track.getSettings() : {}; } catch (_) { trackSettings = {}; }
    try { trackCaps = track.getCapabilities ? track.getCapabilities() : {}; } catch (_) { trackCaps = {}; }
  }
  function configureTrack(track, run) {
    readTrack(track);
    const range = zoomRange();
    if (range && track.applyConstraints) {
      zoom.min = range.min; zoom.max = range.max; zoom.step = range.step > 0 ? range.step : .1;
      zoom.value = Math.min(range.max, Math.max(range.min, trackSettings.zoom || range.min));
      find('zoom-value').textContent = `${Number(trackSettings.zoom || zoom.value).toFixed(1)}x`;
      zoom.disabled = false; find('zoom-control').hidden = false;
    } else { trackCaps.zoom = null; find('zoom-control').hidden = true; }
    if (Array.isArray(trackCaps.focusMode) && trackCaps.focusMode.includes('continuous') && track.applyConstraints) {
      // Optional device autofocus must never prevent capture or reveal device errors.
      track.applyConstraints({advanced: [{focusMode: 'continuous'}]}).then(() => {
        if (run === generation) readTrack(track);
      }).catch(() => {});
    }
  }
  async function applyZoom() {
    const track = stream && stream.getVideoTracks()[0], range = zoomRange(), run = generation;
    if (!track || !range || busy) return;
    if (zoomPending) { zoomTimer = setTimeout(applyZoom, 150); return; }
    const stepSize = range.step > 0 ? range.step : .1;
    const requested = Number(zoom.value);
    const value = Math.min(range.max, Math.max(range.min, range.min + Math.round((requested - range.min) / stepSize) * stepSize));
    zoomPending = true;
    try {
      await withTimeout(track.applyConstraints({advanced: [{zoom: value}]}), 2500);
      if (run !== generation) return;
      readTrack(track);
      find('zoom-value').textContent = `${Number(trackSettings.zoom || value).toFixed(1)}x`;
      updateGuide();
    } catch (_) {
      if (run === generation) { trackCaps.zoom = null; find('zoom-control').hidden = true; status.textContent = 'Continua sin zoom y ajusta la distancia del documento.'; }
    } finally { zoomPending = false; }
  }
  zoom.addEventListener('input', () => { clearTimeout(zoomTimer); zoomTimer = setTimeout(applyZoom, 150); });
  zoom.addEventListener('change', () => { clearTimeout(zoomTimer); applyZoom(); });
  function canUsePhoto() { return !!blob && !!quality && (quality.decision === 'pass' || (quality.decision === 'review' && find('confirm-quality').checked)); }
  find('confirm-quality').addEventListener('change', () => { accept.disabled = busy || !canUsePhoto(); });
  function orientCamera() {
    mobile.classList.toggle('camera-landscape', orientation.matches);
    find('rotate').hidden = !orientation.matches;
    if (orientation.matches && ['opening', 'live', 'checking'].includes(step)) {
      resumePortrait = true; stopCamera(); clearPhoto(); show('ready');
    } else if (!orientation.matches && resumePortrait && !terminal && !document.hidden) {
      if (!busy) { resumePortrait = false; launchCamera(); }
    }
  }
  orientation.addEventListener('change', orientCamera);
  function end(state, data = {}) {
    terminal = true; clearInterval(monitor); stopCamera(); clearPhoto(); token = '';
    const completed = ['FINALIZADA', 'UTILIZADA'].includes(state);
    show(completed ? 'done' : 'error', completed
      ? (delegated ? 'Captura completada. Puedes cerrar esta p\u00e1gina y continuar en tu computador.'
                   : 'C\u00e9dula capturada correctamente. Regresando a tu solicitud...')
      : state === 'EXPIRADA' ? 'El enlace venci\u00f3. Vuelve a tu solicitud y genera uno nuevo.'
        : state === 'NO_DISPONIBLE'
          ? 'La captura expir\u00f3 o ya no est\u00e1 disponible. Revisa tu solicitud en el equipo de origen.'
          : 'Este enlace fue revocado. Vuelve a tu solicitud y genera uno nuevo.');
    if (state === 'FINALIZADA' && !delegated && data.retorno && data.retorno === mobile.dataset.retornoSeguro) {
      const destination = new URL(data.retorno, location.href);
      if (destination.origin !== location.origin) return;
      if (window.parent !== window) {
        window.parent.postMessage({type: 'captura-finalizada', id: mobile.dataset.sessionId}, location.origin);
      } else {
        storage.set(`captura:${mobile.dataset.producto}:${mobile.dataset.contexto || ''}`, mobile.dataset.sessionId);
        location.replace(destination.href);
      }
    }
  }
  function checkTerminal(data) {
    if (['FINALIZADA', 'UTILIZADA', 'EXPIRADA', 'REVOCADA'].includes(data.estado)) {
      end(data.estado, data); return true;
    }
    return false;
  }
  function nextSide(data) {
    const sides = data.lados || [];
    side = sides.includes('FRONTAL') ? 'TRASERA' : 'FRONTAL';
    show(sides.includes('FRONTAL') && sides.includes('TRASERA') ? 'finish' : 'ready',
      sides.length ? 'Foto recibida correctamente.' : '');
  }
  async function action(name, data = new FormData()) {
    if (mobile.dataset.contexto) data.set('solicitud_id', mobile.dataset.contexto);
    return requestJSON(mobile.dataset.base + (delegated ? name.toLowerCase() : name) + '/', data, mobile);
  }
  async function readState() {
    return requestJSON(`${mobile.dataset.base}estado/?solicitud_id=${encodeURIComponent(mobile.dataset.contexto || '')}`, null, mobile);
  }
  async function monitorState() {
    if (terminal || checking || document.hidden || (delegated && token && !redeemed)) return;
    checking = true;
    try {
      const data = await readState();
      if (!checkTerminal(data) && delegated && !redeemed && !busy) {
        redeemed = true;
        nextSide(data);
      }
    }
    catch (error) {
      if (error.grantInvalid) { end('NO_DISPONIBLE'); return; }
      if (!terminal) {
        stopCamera();
        show(blob ? 'preview' : step === 'finish' ? 'finish' : 'ready',
          'No pudimos comprobar la vigencia. Revisa tu conexi\u00f3n y vuelve a intentar.');
      }
    } finally { checking = false; }
  }
  function cameraError(error) {
    const messages = {
      NotAllowedError: 'Permite el acceso a la c\u00e1mara en tu navegador y vuelve a intentar.',
      NotFoundError: 'No encontramos una c\u00e1mara. Abre el enlace desde un celular con c\u00e1mara disponible.',
      NotReadableError: 'La c\u00e1mara puede estar ocupada. Cierra otras aplicaciones que la usen e intenta de nuevo.',
    };
    return messages[error.name] || 'No pudimos iniciar la c\u00e1mara. Vuelve a intentar desde un navegador compatible.';
  }
  function launchCamera() {
    stopCamera(); clearPhoto();
    if (orientation.matches) { resumePortrait = true; show('ready'); orientCamera(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      show('ready', 'Abre el enlace seguro desde un celular o navegador compatible con c\u00e1mara.'); return;
    }
    const run = generation;
    show('opening', 'Permite el acceso a la c\u00e1mara para continuar.');
    // getUserMedia cannot be aborted. Late streams must be stopped, never attached.
    cameraTimer = setTimeout(() => {
      if (run !== generation) return;
      stopCamera(); show('ready', 'No recibimos acceso a la c\u00e1mara. Revisa el permiso y vuelve a intentar.');
    }, 30000);
    navigator.mediaDevices.getUserMedia({audio: false, video: {
      facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080},
    }}).then(async media => {
      if (run !== generation || terminal || document.hidden) {
        media.getTracks().forEach(track => track.stop()); return;
      }
      stream = media; video.srcObject = media; video.muted = true;
      show('live', 'Encuadra el documento y toma la foto.');
      await video.play();
      if (run !== generation) return;
      clearTimeout(cameraTimer); take.disabled = false;
      configureTrack(media.getVideoTracks()[0], run);
      updateGuide();
      media.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
        if (stream === media) { stopCamera(); show('ready', 'La c\u00e1mara se desconect\u00f3. Vuelve a abrirla.'); }
      }));
    }).catch(error => {
      if (run !== generation) return;
      stopCamera(); show('ready', cameraError(error));
    });
  }
  async function operation(callback) {
    if (busy || terminal) return;
    busy = true; buttons.forEach(button => { button.disabled = true; });
    mobile.setAttribute('aria-busy', 'true');
    try { await callback(); }
    catch (error) {
      stopCamera();
      if (error.grantInvalid) { end('NO_DISPONIBLE'); return; }
      // A rejected upload may mean an expired/revoked session. Consult, never regenerate.
      try { if (checkTerminal(await readState())) return; } catch (_) { /* Keep a safe retry message. */ }
      if (!terminal) show(blob ? 'preview' : step === 'finish' ? 'finish' : 'ready',
        typeof error.retryable === 'boolean' ? error.message : 'No pudimos completar la captura. Vuelve a intentar.');
    } finally {
      busy = false; buttons.forEach(button => { button.disabled = false; });
      accept.disabled = !canUsePhoto();
      take.disabled = step !== 'live' || video.readyState < 2;
      mobile.setAttribute('aria-busy', 'false');
      if (resumePortrait && !orientation.matches) orientCamera();
    }
  }
  open.addEventListener('click', () => operation(async () => {
    let data;
    if (!delegated || !token) {
      data = await readState();
      if (checkTerminal(data)) return;
    }
    if (token && (!data || data.estado === 'ABIERTA')) {
      const body = new FormData(); body.set('token', token);
      data = await action('canjear', body); token = '';
      history.replaceState(null, '', location.pathname + location.search);
    }
    token = '';
    if (terminal || checkTerminal(data)) return;
    redeemed = true; nextSide(data);
    if (step !== 'finish' && !document.hidden) launchCamera();
  }));
  repeat.addEventListener('click', () => { if (!busy && !terminal) launchCamera(); });
  exitCamera.addEventListener('click', () => {
    resumePortrait = false;
    stopCamera(); clearPhoto();
    if (!terminal) show('ready', 'Puedes abrir la camara de nuevo para continuar.');
  });

  const MAX_BYTES = 8 * 1024 * 1024, MAX_PIXELS = 20000000;
  function withTimeout(promise, milliseconds) {
    let timer;
    return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(problem('La camara tardo demasiado. Intenta nuevamente.')), milliseconds); })])
      .finally(() => clearTimeout(timer));
  }
  async function encodePhoto(source, width, height) {
    // Every attempt draws from the original, never from a previously compressed JPEG.
    let scale = Math.min(1, Math.sqrt(MAX_PIXELS / (width * height)));
    const canvas = document.createElement('canvas');
    try {
      for (let attempt = 0; attempt < 6; attempt++) {
        canvas.width = Math.max(1, Math.floor(width * scale)); canvas.height = Math.max(1, Math.floor(height * scale));
        const context = canvas.getContext('2d');
        if (!context) throw problem('No pudimos preparar la foto. Abre la camara e intenta nuevamente.');
        context.drawImage(source, 0, 0, canvas.width, canvas.height);
        const photo = await withTimeout(new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .95)), 6000);
        if (!photo || !photo.size) throw problem('No pudimos guardar la foto. Intenta nuevamente.');
        if (photo.size <= MAX_BYTES) return photo;
        scale *= Math.min(.85, Math.sqrt(MAX_BYTES / photo.size) * .95);
      }
      throw problem('La foto es demasiado grande. Repite la captura.');
    } finally { canvas.width = canvas.height = 0; }
  }
  async function normalizeNative(photo) {
    if (!photo || !photo.size) throw problem('No pudimos guardar la foto. Intenta nuevamente.');
    const url = URL.createObjectURL(photo), image = new Image();
    try {
      await withTimeout(new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = url; }), 6000);
      if (!image.naturalWidth || !image.naturalHeight) throw problem('Foto no disponible.');
      if (['image/jpeg', 'image/png', 'image/webp'].includes(photo.type) && photo.size <= MAX_BYTES &&
          image.naturalWidth * image.naturalHeight <= MAX_PIXELS) return photo;
      return await encodePhoto(image, image.naturalWidth, image.naturalHeight);
    } finally { image.onload = image.onerror = null; image.src = ''; URL.revokeObjectURL(url); }
  }
  async function capturePhoto(run) {
    const track = stream.getVideoTracks()[0];
    if (typeof window.ImageCapture === 'function') {
      try {
        const capture = new ImageCapture(track);
        if (typeof capture.takePhoto === 'function') {
          const native = await withTimeout(capture.takePhoto(), 6000);
          if (run !== generation || terminal || document.hidden) return null;
          return await normalizeNative(native);
        }
      } catch (_) { /* Unsupported/failed still capture falls back to the live frame. */ }
    }
    if (run !== generation || terminal || document.hidden) return null;
    return encodePhoto(video, video.videoWidth, video.videoHeight);
  }
  take.addEventListener('click', () => operation(async () => {
    if (!stream || !video.videoWidth || !video.videoHeight) throw problem('Espera a que la c\u00e1mara muestre el documento.');
    const run = generation;
    const geometry = {expected: expectedFrame, aspect: video.videoWidth / video.videoHeight};
    let photo, current;
    try { photo = await capturePhoto(run); }
    finally { current = run === generation; if (current) stopCamera(); }
    if (!current || terminal || document.hidden) return;
    if (!photo || !photo.size) throw problem('No pudimos tomar la foto. Abre la c\u00e1mara y repite la captura.');
    blob = photo; previewURL = URL.createObjectURL(blob); preview.src = previewURL;
    const analysisRun = generation;
    show('checking', 'Revisando la foto...');
    try {
      quality = await withTimeout(window.DocumentCaptureQuality.analyze(photo, geometry), 5000);
      if (!quality || !['pass', 'reject', 'review'].includes(quality.decision)) throw new Error();
    } catch (_) {
      quality = {decision: 'review', message: 'No pudimos revisar la calidad. Repite o revisa la nitidez y las cuatro esquinas antes de continuar.'};
    }
    if (analysisRun !== generation || terminal || document.hidden) { quality = null; return; }
    show('preview', quality.message || 'Revisa que toda la informaci\u00f3n est\u00e9 n\u00edtida y completa.');
  }));
  accept.addEventListener('click', () => operation(async () => {
    if (!canUsePhoto()) return;
    status.textContent = 'Enviando foto de forma segura...';
    const extension = blob.type === 'image/png' ? 'png' : blob.type === 'image/webp' ? 'webp' : 'jpg';
    const body = new FormData(); body.set('archivo', blob, `captura.${extension}`);
    const data = await action(side, body);
    if (terminal || checkTerminal(data)) return;
    clearPhoto(); nextSide(data);
  }));
  finish.addEventListener('click', () => operation(async () => {
    stopCamera();
    status.textContent = 'Finalizando captura...';
    const data = await action('finalizar');
    if (!checkTerminal(data)) throw problem('No pudimos confirmar la captura. Intenta finalizar nuevamente.');
  }));
  function suspend() {
    stopCamera();
    if (step === 'checking') { clearPhoto(); show('ready'); }
    if (!terminal && ['live', 'opening'].includes(step)) show('ready', 'La c\u00e1mara se paus\u00f3. Abrela de nuevo para continuar.');
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) suspend(); else monitorState(); });
  window.addEventListener('pagehide', () => {
    suspend(); clearPhoto(); clearInterval(monitor);
    if (!terminal && step === 'preview') show('ready', 'Abre la c\u00e1mara para repetir la foto.');
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted && !terminal) { monitor = setInterval(monitorState, 10000); monitorState(); }
  });
  show('ready'); orientCamera(); monitor = setInterval(monitorState, 10000); monitorState();
})();
