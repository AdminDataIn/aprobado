(() => {
  'use strict';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const storage = {
    get(key) { try { return sessionStorage.getItem(key) || ''; } catch (_) { return ''; } },
    set(key, value) { try { sessionStorage.setItem(key, value); } catch (_) { /* Optional resume only. */ } },
  };
  const problem = (message, retryable = false) => Object.assign(new Error(message), {retryable});
  async function requestJSON(url, body, scope) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const endpoint = new URL(url, location.href);
      if (endpoint.origin !== location.origin) throw problem('No pudimos abrir la captura segura. Recarga la pagina.');
      const options = {credentials: 'same-origin', mode: 'same-origin', cache: 'no-store',
        referrerPolicy: 'no-referrer', signal: controller.signal, headers: {Accept: 'application/json'}};
      if (body) {
        const csrf = scope.querySelector('[name=csrfmiddlewaretoken]');
        if (!csrf || !csrf.value) throw problem('Recarga la p\u00e1gina para renovar la sesi\u00f3n e int\u00e9ntalo de nuevo.');
        options.method = 'POST'; options.body = body;
        options.headers['X-CSRFToken'] = csrf.value;
      }
      const response = await fetch(endpoint.href, options);
      if (response.redirected || response.status === 401) throw problem('Tu sesi\u00f3n termin\u00f3. Inicia sesi\u00f3n de nuevo y vuelve a tu solicitud.');
      if (response.status === 403) throw problem('No pudimos autorizar la operaci\u00f3n. Recarga la p\u00e1gina e inicia sesi\u00f3n con tu cuenta.');
      if (response.status === 429) throw problem('Has realizado varios intentos. Espera un momento antes de reintentar.', true);
      if (response.status >= 500) throw problem('La captura no est\u00e1 disponible por un momento. Int\u00e9ntalo de nuevo.', true);
      if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
        throw problem('No recibimos la respuesta esperada. Revisa tu sesi\u00f3n y vuelve a intentarlo.');
      }
      let data;
      try { data = await response.json(); } catch (_) { throw problem('No pudimos confirmar la respuesta. Intentalo de nuevo.', true); }
      if (!response.ok) throw problem('No se pudo completar la operacion. Comprueba que el enlace siga vigente y que uses la misma cuenta.');
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
    const form = panel.closest('form') || document;
    const context = panel.dataset.contexto || '';
    const base = panel.dataset.crear.replace(/crear\/$/, '');
    const key = `captura:${panel.dataset.producto}:${context}`;
    let timer, expiryTimer, cycle = 0, attempts = 0, failures = 0, busy = false;
    let currentState = '', secureLink = '';
    const mobile = window.matchMedia('(max-width: 640px), (pointer: coarse) and (max-width: 1024px)');
    function adaptScreen() {
      panel.dataset.mobile = String(mobile.matches);
      field('capture-title').textContent = mobile.matches ? 'Captura tu documento de identidad' : 'Documento de identidad';
      field('capture-description').textContent = mobile.matches
        ? 'Abre la captura segura en este celular. Tu solicitud permanecer\u00e1 abierta; vuelve a esta pesta\u00f1a al terminar.'
        : 'Para proteger tu informaci\u00f3n, la captura de tu documento se realiza desde tu celular. No perder\u00e1s la informaci\u00f3n que ya diligenciaste.';
      if (!hidden.value) create.textContent = mobile.matches ? 'Preparar captura segura' : 'Continuar desde mi celular';
    }
    function paint(state, message) { panel.dataset.state = state; status.textContent = message; }
    function clearLink() {
      clearTimeout(expiryTimer);
      secureLink = '';
      link.removeAttribute('href');
      result.hidden = true;
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    }
    function applyState(state) {
      currentState = state;
      if (['FINALIZADA', 'UTILIZADA', 'EXPIRADA', 'REVOCADA'].includes(state)) {
        clearTimeout(timer); clearLink();
        retry.hidden = true;
        create.hidden = ['FINALIZADA', 'UTILIZADA'].includes(state);
        if (state === 'FINALIZADA' || state === 'UTILIZADA') {
          paint('success', state === 'FINALIZADA' ? 'Documento de identidad capturado correctamente' : 'Documento de identidad vinculado a tu solicitud.');
          if (state === 'FINALIZADA') {
            ['cedula_frontal', 'cedula_trasera', 'id_documento_identidad_frontal', 'id_documento_identidad_reverso'].forEach(id => {
              const input = document.getElementById(id);
              if (input && form.contains(input)) { input.required = false; input.dataset.existing = 'true'; }
            });
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
            url.pathname !== `${base}${data.id}/` || !Number.isFinite(expires.getTime())) {
          throw problem('No pudimos preparar el enlace seguro. Intentalo nuevamente.');
        }
        hidden.value = data.id; storage.set(key, data.id);
        secureLink = url.href; link.href = secureLink;
        drawQR(secureLink);
        expiry.textContent = `Enlace v\u00e1lido hasta ${expires.toLocaleTimeString('es-CO', {hour: '2-digit', minute: '2-digit'})}.`;
        result.hidden = false; create.hidden = true;
        applyState('ABIERTA');
        clearTimeout(expiryTimer);
        const issuedId = data.id;
        expiryTimer = setTimeout(() => {
          if (hidden.value === issuedId) { ++cycle; applyState('EXPIRADA'); }
        }, Math.max(0, expires.getTime() - Date.now()));
        attempts = failures = 0; poll(run);
      } catch (error) {
        paint('error', error instanceof TypeError ? 'No pudimos preparar el enlace. Intentalo nuevamente.' : error.message);
        create.hidden = false; create.textContent = 'Reintentar';
        retry.hidden = !UUID.test(hidden.value);
      } finally { busy = create.disabled = false; panel.setAttribute('aria-busy', 'false'); }
    });
    copy.addEventListener('click', async () => {
      if (!secureLink) return;
      try { await navigator.clipboard.writeText(secureLink); paint('waiting', 'Enlace copiado. Abrelo en tu celular.'); }
      catch (_) { paint('waiting', 'Manten pulsado Abrir captura, o usa su menu, para copiar el enlace.'); }
    });
    retry.addEventListener('click', restartPoll);
    mobile.addEventListener('change', adaptScreen);
    // Listener installation precedes all optional storage access.
    const saved = hidden.value || storage.get(key);
    hidden.value = UUID.test(saved) ? saved : '';
    adaptScreen();
    if (hidden.value) {
      create.hidden = true; paint('loading', 'Consultando tu captura pendiente...'); restartPoll();
    }
  }
  document.querySelectorAll('[data-capture-handoff]').forEach(initHandoff);

  const mobile = document.querySelector('[data-capture-mobile]');
  if (!mobile || mobile.dataset.captureReady) return;
  mobile.dataset.captureReady = 'true';
  let token = location.hash.slice(1);
  history.replaceState(null, '', location.pathname + location.search);
  const status = mobile.querySelector('[data-capture-status]');
  const find = name => mobile.querySelector(`[data-${name}]`);
  const video = find('camera-video'), preview = find('camera-preview');
  const open = find('canjear'), take = find('tomar-foto'), repeat = find('repetir');
  const accept = find('usar-foto'), finish = find('finalizar');
  const buttons = [open, take, repeat, accept, finish];
  let stream, blob, previewURL, monitor, cameraTimer, generation = 0;
  let step = 'ready', side = 'FRONTAL', busy = false, terminal = false, redeemed = false;
  let checking = false;

  function stopCamera() {
    ++generation;
    clearTimeout(cameraTimer);
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
  }
  function clearPhoto() {
    blob = null;
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
    preview.removeAttribute('src');
  }
  function show(next, message = '') {
    step = next; mobile.dataset.state = next;
    status.textContent = message;
    find('viewfinder').hidden = next !== 'live';
    preview.hidden = next !== 'preview';
    open.hidden = next !== 'ready'; take.hidden = next !== 'live';
    repeat.hidden = accept.hidden = next !== 'preview'; finish.hidden = next !== 'finish';
    open.textContent = redeemed ? 'Abrir c\u00e1mara de nuevo' : 'Abrir c\u00e1mara';
    take.disabled = next !== 'live' || video.readyState < 2;
    find('camera-step').textContent = side === 'FRONTAL' ? '1 de 2 \u00b7 Frontal' : '2 de 2 \u00b7 Posterior';
    find('camera-title').textContent = next === 'finish' || next === 'done'
      ? 'Ambas caras recibidas' : side === 'FRONTAL' ? 'Frente del documento' : 'Reverso del documento';
    find('camera-hint').hidden = ['finish', 'done', 'error'].includes(next);
  }
  function end(state) {
    terminal = true; clearInterval(monitor); stopCamera(); clearPhoto(); token = '';
    const completed = ['FINALIZADA', 'UTILIZADA'].includes(state);
    show(completed ? 'done' : 'error', completed
      ? 'Documento de identidad capturado correctamente. Vuelve a tu solicitud para continuar.'
      : state === 'EXPIRADA' ? 'El enlace venci\u00f3. Vuelve a tu solicitud y genera uno nuevo.'
        : 'Este enlace fue revocado. Vuelve a tu solicitud y genera uno nuevo.');
  }
  function checkTerminal(data) {
    if (['FINALIZADA', 'UTILIZADA', 'EXPIRADA', 'REVOCADA'].includes(data.estado)) {
      end(data.estado); return true;
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
    return requestJSON(mobile.dataset.base + name + '/', data, mobile);
  }
  async function readState() {
    return requestJSON(`${mobile.dataset.base}estado/?solicitud_id=${encodeURIComponent(mobile.dataset.contexto || '')}`, null, mobile);
  }
  async function monitorState() {
    if (terminal || checking || document.hidden) return;
    checking = true;
    try { checkTerminal(await readState()); }
    catch (_) {
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
      // A rejected upload may mean an expired/revoked session. Consult, never regenerate.
      try { if (checkTerminal(await readState())) return; } catch (_) { /* Keep a safe retry message. */ }
      if (!terminal) show(blob ? 'preview' : step === 'finish' ? 'finish' : 'ready',
        typeof error.retryable === 'boolean' ? error.message : 'No pudimos completar la captura. Vuelve a intentar.');
    } finally {
      busy = false; buttons.forEach(button => { button.disabled = false; });
      take.disabled = step !== 'live' || video.readyState < 2;
      mobile.setAttribute('aria-busy', 'false');
    }
  }
  open.addEventListener('click', () => operation(async () => {
    let data = await readState();
    if (checkTerminal(data)) return;
    if (token) {
      const body = new FormData(); body.set('token', token);
      data = await action('canjear', body); token = '';
    }
    if (terminal || checkTerminal(data)) return;
    redeemed = true; nextSide(data);
    if (step !== 'finish' && !document.hidden) launchCamera();
  }));
  repeat.addEventListener('click', () => { if (!busy && !terminal) launchCamera(); });
  take.addEventListener('click', () => operation(async () => {
    if (!stream || !video.videoWidth || !video.videoHeight) throw problem('Espera a que la c\u00e1mara muestre el documento.');
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 2048 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    stopCamera();
    const run = generation;
    const photo = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .92));
    canvas.width = canvas.height = 0;
    if (terminal || run !== generation || document.hidden) return;
    if (!photo || !photo.size) throw problem('No pudimos tomar la foto. Abre la c\u00e1mara y repite la captura.');
    blob = photo; previewURL = URL.createObjectURL(blob); preview.src = previewURL;
    show('preview', 'Revisa que toda la informaci\u00f3n est\u00e9 n\u00edtida y completa.');
  }));
  accept.addEventListener('click', () => operation(async () => {
    if (!blob) return;
    status.textContent = 'Enviando foto de forma segura...';
    const body = new FormData(); body.set('archivo', blob, 'captura.jpg');
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
  show('ready'); monitor = setInterval(monitorState, 10000); monitorState();
})();
