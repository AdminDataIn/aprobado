(function () {
  'use strict';
  document.querySelectorAll('[data-evaluation-tracking]').forEach(function (root) {
    if (root.dataset.poll !== 'true') return;
    var message = root.querySelector('[data-evaluation-message]');
    var spinner = root.querySelector('[data-evaluation-spinner]');
    var label = root.querySelector('[data-evaluation-label]');
    var detail = root.querySelector('[data-evaluation-detail]');
    var deadline = Date.now() + 120000;
    var delay = 3000;
    var timer;
    var controller;
    var active = true;
    var busy = false;

    function stop() {
      active = false;
      window.clearTimeout(timer);
      if (controller) controller.abort();
      spinner.hidden = true;
      document.removeEventListener('visibilitychange', visibility);
    }
    function schedule() {
      window.clearTimeout(timer);
      if (!active) return;
      if (Date.now() >= deadline) {
        stop();
        message.textContent = 'Puedes volver más tarde para consultar el avance de tu solicitud.';
        return;
      }
      if (!document.hidden) timer = window.setTimeout(poll, Math.min(delay, deadline - Date.now()));
    }
    async function poll() {
      if (!active || busy || document.hidden) return;
      if (Date.now() >= deadline) { schedule(); return; }
      busy = true;
      controller = new AbortController();
      var timeout = window.setTimeout(function () { controller.abort(); }, 10000);
      try {
        var response = await fetch(root.dataset.stateUrl, {
          credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal,
          headers: {'Accept': 'application/json'}
        });
        if ([401, 403, 404].includes(response.status)) { stop(); return; }
        if (!response.ok) throw new Error('unavailable');
        var data = await response.json();
        if (!active) return;
        message.textContent = data.mensaje;
        if (label) label.textContent = data.etiqueta;
        if (detail) detail.textContent = data.detalle;
        spinner.hidden = !data.procesando;
        if (!data.continuar_seguimiento) stop();
      } catch (error) {
        if (active && !document.hidden) {
          spinner.hidden = true;
          message.textContent = 'No pudimos actualizar el estado. Puedes consultar de nuevo más tarde.';
        }
      } finally {
        window.clearTimeout(timeout);
        controller = null;
        busy = false;
        delay = Math.min(15000, delay * 1.5);
        schedule();
      }
    }
    function visibility() {
      window.clearTimeout(timer);
      if (document.hidden) { if (controller) controller.abort(); }
      else schedule();
    }
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', stop, {once: true});
    schedule();
  });
}());
