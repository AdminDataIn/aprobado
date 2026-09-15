(() => {
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]').value;
  async function post(url, data) {
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'X-CSRFToken': csrf()}, body: data});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo completar la operacion.');
    return result;
  }
  document.querySelectorAll('[data-capture-handoff]').forEach(panel => {
    const status = panel.querySelector('[data-capture-status]');
    const hidden = panel.querySelector('[data-sesion-documental]');
    const context = panel.dataset.contexto;
    const base = panel.dataset.crear.replace(/crear\/$/, '');
    const key = `captura:${panel.dataset.producto}:${context}`;
    hidden.value = hidden.value || sessionStorage.getItem(key) || '';
    const link = panel.querySelector('[data-enlace-captura]');
    const copy = panel.querySelector('[data-copiar-enlace]');
    panel.querySelector('[data-crear-enlace]').addEventListener('click', async event => {
      event.target.disabled = true;
      try {
        const body = new FormData();
        if (context) body.set('solicitud_id', context);
        const data = await post(panel.dataset.crear, body);
        hidden.value = data.id;
        sessionStorage.setItem(key, data.id);
        link.href = data.enlace;
        link.hidden = copy.hidden = false;
        status.textContent = `Enlace vigente hasta ${new Date(data.expira_en).toLocaleTimeString('es-CO', {hour: '2-digit', minute: '2-digit'})}. Inicia sesion con tu misma cuenta.`;
        polls = 0;
        poll();
      } catch (error) { status.textContent = error.message; }
      finally { event.target.disabled = false; }
    });
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(link.href); status.textContent = 'Enlace copiado.'; }
      catch (_) { status.textContent = 'Abre el enlace o copialo desde su menu.'; }
    });
    let polls = 0, timer;
    async function poll() {
      clearTimeout(timer);
      if (!hidden.value || polls++ >= 120) return;
      try {
        const response = await fetch(`${base}${hidden.value}/estado/?solicitud_id=${encodeURIComponent(context)}`);
        const data = await response.json();
        if (!response.ok) throw new Error('La sesion documental no esta disponible.');
        status.textContent = data.estado === 'FINALIZADA' ? 'Ambas caras recibidas. Puedes continuar la solicitud.' : `Captura: ${data.estado}`;
        if (data.estado === 'FINALIZADA') {
          const form = panel.closest('form');
          ['cedula_frontal', 'cedula_trasera', 'id_documento_identidad_frontal', 'id_documento_identidad_reverso'].forEach(id => {
            const input = document.getElementById(id);
            if (input && form.contains(input)) { input.required = false; input.dataset.existing = 'true'; }
          });
          return;
        }
        if (['EXPIRADA', 'REVOCADA', 'UTILIZADA'].includes(data.estado)) return;
        timer = setTimeout(poll, 5000);
      } catch (error) { status.textContent = error.message; }
    }
    poll();
  });
  const mobile = document.querySelector('[data-capture-mobile]');
  if (!mobile) return;
  let token = location.hash.slice(1);
  history.replaceState(null, '', location.pathname + location.search);
  const status = mobile.querySelector('[data-capture-status]');
  // Senal UX, no acreditacion de dispositivo. El backend exige cuenta y canje.
  const mobileUX = matchMedia('(pointer: coarse)').matches && matchMedia('(max-width: 1024px)').matches;
  mobile.querySelector('[data-mobile-controls]').hidden = !mobileUX;
  mobile.querySelector('[data-desktop-message]').hidden = mobileUX;
  async function action(name, data = new FormData()) {
    if (mobile.dataset.contexto) data.set('solicitud_id', mobile.dataset.contexto);
    return post(mobile.dataset.base + name + '/', data);
  }
  mobile.querySelector('[data-canjear]').addEventListener('click', async event => {
    event.target.disabled = true;
    try {
      if (token) {
        const data = new FormData(); data.set('token', token);
        await action('canjear', data); token = '';
      }
      mobile.querySelector('[data-lados]').hidden = false;
      status.textContent = 'Selecciona cada cara del documento.';
    } catch (error) { status.textContent = error.message; event.target.disabled = false; }
  });
  mobile.querySelectorAll('[data-lado]').forEach(input => input.addEventListener('change', async () => {
    if (!input.files[0]) return;
    input.disabled = true;
    try {
      const data = new FormData(); data.set('archivo', input.files[0]);
      await action(input.dataset.lado, data);
      status.textContent = `${input.dataset.lado}: archivo recibido.`;
    } catch (error) { status.textContent = error.message; }
    finally { input.disabled = false; }
  }));
  mobile.querySelector('[data-finalizar]').addEventListener('click', async event => {
    event.target.disabled = true;
    try { await action('finalizar'); status.textContent = 'Documentos recibidos. Vuelve a tu solicitud para continuar.'; }
    catch (error) { status.textContent = error.message; event.target.disabled = false; }
  });
})();
