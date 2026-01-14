// ===== LOADER GLOBAL =====
const globalLoader = document.getElementById('globalLoader');
let lottieAnimation;

// Funciones para mostrar/ocultar loader
function showLoader(text = 'Cargando...') {
    const loaderText = document.querySelector('.loader-text');
    if (loaderText) loaderText.textContent = text;
    globalLoader.classList.add('active');
}

function hideLoader() {
    globalLoader.classList.remove('active');
}

function getFilenameFromDisposition(disposition) {
    if (!disposition) return null;
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match && utf8Match[1]) {
        return decodeURIComponent(utf8Match[1]);
    }
    const quotedMatch = disposition.match(/filename="([^"]+)"/i);
    if (quotedMatch && quotedMatch[1]) {
        return quotedMatch[1];
    }
    const plainMatch = disposition.match(/filename=([^;]+)/i);
    if (plainMatch && plainMatch[1]) {
        return plainMatch[1].trim();
    }
    return null;
}

async function downloadWithLoader(link) {
    const url = link.href;
    showLoader('Descargando...');
    try {
        const response = await fetch(url, { credentials: 'same-origin' });
        if (!response.ok) {
            throw new Error(`Download failed: ${response.status}`);
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition');
        const filename = getFilenameFromDisposition(disposition) || 'archivo';
        const blobUrl = window.URL.createObjectURL(blob);
        const tempLink = document.createElement('a');
        tempLink.href = blobUrl;
        tempLink.download = filename;
        document.body.appendChild(tempLink);
        tempLink.click();
        tempLink.remove();
        setTimeout(() => window.URL.revokeObjectURL(blobUrl), 1000);
    } catch (error) {
        console.error('Error downloading file:', error);
        window.location.href = url;
    } finally {
        hideLoader();
    }
}

// Mostrar loader en navegación entre páginas
window.addEventListener('beforeunload', function() {
    showLoader('Cargando...');
});

// Ocultar loader cuando la página carga
window.addEventListener('load', function() {
    hideLoader();
});

// Mostrar loader en clics de links (excepto # y javascript:)
document.addEventListener('click', function(e) {
    const link = e.target.closest('a');
    if (!link || !link.href) return;
    if (link.dataset && link.dataset.download === 'true') {
        e.preventDefault();
        downloadWithLoader(link);
        return;
    }
    if (!link.href.includes('#') && !link.href.includes('javascript:') && link.target !== '_blank') {
        showLoader('Cargando...');
    }
});

// Función para usar en formularios
function submitWithLoader(form, loaderText = 'Procesando...') {
    showLoader(loaderText);
    form.submit();
}

function cambiarCredito(creditoId) {
    if (creditoId) {
        window.location.href = `/emprendimiento/mi-credito/${creditoId}/`;
    }
}

// Actualizar año en el footer dinámicamente
document.addEventListener('DOMContentLoaded', function() {
    const yearElement = document.querySelector('.footer-aprobado p');
    if (yearElement) {
        yearElement.textContent = `© ${new Date().getFullYear()} Aprobado. Todos los derechos reservados.`;
    }
});

// ===== MODAL DE ABONOS =====
function initModalAbonos(config) {
    const {
        creditoId,
        csrfToken,
        valorCuota,
        capitalPendiente,
        saldoPendiente
    } = config;

    console.log('Inicializando modal de pagos...');
    const abonoModal = document.getElementById('abonoModal');
    const tipoAbonoCards = document.querySelectorAll('.tipo-abono-card-modern');
    console.log('Cards encontradas:', tipoAbonoCards.length);
    const formCapital = document.getElementById('form-capital');
    const formNormal = document.getElementById('form-normal');
    const formTotal = document.getElementById('form-total');
    const btnAnalizarContainer = document.getElementById('btn-analizar-container');
    const btnAnalizar = document.getElementById('btn-analizar-abono');
    const btnConfirmar = document.getElementById('btn-confirmar-abono');
    const resultadosAnalisis = document.getElementById('resultados-analisis');
    const errorAnalisis = document.getElementById('error-analisis');
    const comparacionPlanes = document.getElementById('comparacion-planes');
    const montoCapitalInput = document.getElementById('monto-capital');
    const montoNormalInput = document.getElementById('monto-normal');
    const cuotaActualEl = document.getElementById('cuota-actual');
    const abonoNormalEl = document.getElementById('abono-normal');
    const cuotaRestanteEl = document.getElementById('cuota-restante');
    const normalErrorEl = document.getElementById('normal-error');
    const abonoNormalFormatoEl = document.getElementById('abono-normal-formato');
    const interesesAcumuladosEl = document.getElementById('intereses-acumulados');
    const totalPagarEl = document.getElementById('total-pagar');
    const capitalPendienteTotalEl = document.getElementById('capital-pendiente-total');

    let tipoAbonoSeleccionado = null;
    let datosAnalisis = null;

    // Selección de tipo de pago
    tipoAbonoCards.forEach(card => {
        card.addEventListener('click', function() {
            const tipo = this.dataset.tipo;

            // Reset visual
            tipoAbonoCards.forEach(c => {
                c.classList.remove('active');
            });

            // Highlight seleccionado
            this.classList.add('active');

            // Mostrar formulario correspondiente
            formCapital.classList.add('d-none');
            formNormal.classList.add('d-none');
            formTotal.classList.add('d-none');
            resultadosAnalisis.classList.add('d-none');
            btnConfirmar.classList.add('d-none');
            errorAnalisis.classList.add('d-none');
            if (normalErrorEl) normalErrorEl.classList.add('d-none');

            tipoAbonoSeleccionado = tipo;

            if (tipo === 'CAPITAL') {
                formCapital.classList.remove('d-none');
                btnAnalizarContainer.style.display = 'block';
            } else if (tipo === 'NORMAL') {
                formNormal.classList.remove('d-none');
                btnAnalizarContainer.style.display = 'none';
                btnConfirmar.classList.remove('d-none');
                btnConfirmar.innerHTML = '<i class="bi bi-check-circle me-1"></i>Proceder al Pago';
                actualizarResumenNormal();
            } else if (tipo === 'TOTAL') {
                formTotal.classList.remove('d-none');
                calcularPagoTotal();
                btnAnalizarContainer.style.display = 'none';
                btnConfirmar.classList.remove('d-none');
                btnConfirmar.innerHTML = '<i class="bi bi-check-circle me-1"></i>Proceder al Pago';
            }
        });
    });

    // Calcular pago total
    function calcularPagoTotal() {
        // Hacer fetch para obtener el total exacto con intereses acumulados
        fetch(`/emprendimiento/mi-credito/${creditoId}/calcular-pago-total/`, {
            method: 'GET',
            headers: {
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                if (capitalPendienteTotalEl) {
                    capitalPendienteTotalEl.textContent = '$' + parseFloat(data.capital_pendiente).toLocaleString('es-CO', {
                        minimumFractionDigits: 0,
                        maximumFractionDigits: 0
                    });
                }
                interesesAcumuladosEl.textContent = '$' + parseFloat(data.intereses_acumulados).toLocaleString('es-CO', {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: 0
                });
                totalPagarEl.textContent = '$' + parseFloat(data.total_pagar).toLocaleString('es-CO', {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: 0
                });
                datosAnalisis = data; // Guardar para usar en confirmar
            }
        })
        .catch(error => {
            console.error('Error calculando pago total:', error);
            // Fallback: mostrar solo capital pendiente
            interesesAcumuladosEl.textContent = '$0';
            if (capitalPendienteTotalEl) {
                capitalPendienteTotalEl.textContent = '$' + capitalPendiente.toLocaleString('es-CO', {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: 0
                });
            }
            totalPagarEl.textContent = '$' + capitalPendiente.toLocaleString('es-CO', {
                minimumFractionDigits: 0,
                maximumFractionDigits: 0
            });
        });
    }

    function actualizarResumenNormal() {
        if (!montoNormalInput || !abonoNormalEl || !cuotaRestanteEl) return;

        const monto = parseFloat(montoNormalInput.value || '0');
        const maxCuota = parseFloat(montoNormalInput.dataset.maxCuota || valorCuota || '0');
        const maxSaldo = parseFloat(montoNormalInput.dataset.maxSaldo || saldoPendiente || '0');
        const maxPermitido = Math.min(maxCuota || 0, maxSaldo || 0);

        const montoValido = monto > 0 && monto <= maxPermitido;
        const cuotaBase = Math.min(valorCuota || 0, maxPermitido || valorCuota || 0);
        const cuotaRestante = Math.max(cuotaBase - (montoValido ? monto : 0), 0);

        if (normalErrorEl) {
            if (monto > maxPermitido) {
                normalErrorEl.textContent = 'El abono no puede superar el valor de la cuota ni el saldo pendiente.';
                normalErrorEl.classList.remove('d-none');
            } else {
                normalErrorEl.classList.add('d-none');
            }
        }

        abonoNormalEl.textContent = '$' + (montoValido ? monto : 0).toLocaleString('es-CO', {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        });
        if (abonoNormalFormatoEl) {
            abonoNormalFormatoEl.textContent = '$' + (monto || 0).toLocaleString('es-CO', {
                minimumFractionDigits: 0,
                maximumFractionDigits: 0
            });
        }
        cuotaRestanteEl.textContent = '$' + cuotaRestante.toLocaleString('es-CO', {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        });
    }

    if (montoNormalInput) {
        montoNormalInput.addEventListener('input', actualizarResumenNormal);
    }

    // Analizar abono
    if (btnAnalizar) {
        btnAnalizar.addEventListener('click', function() {
            errorAnalisis.classList.add('d-none');
            comparacionPlanes.classList.add('d-none');

            let monto = 0;
            let tipo = '';
            let numCuotas = null;

            if (tipoAbonoSeleccionado === 'CUOTAS') {
                numCuotas = parseInt(numCuotasSlider.value);
                monto = valorCuota * numCuotas;
                tipo = 'CUOTAS';
            } else if (tipoAbonoSeleccionado === 'CAPITAL') {
                monto = parseFloat(montoCapitalInput.value);
                tipo = 'CAPITAL';

                if (!monto || monto < 50000) {
                    mostrarError('El monto mínimo para abono a capital es $50.000');
                    return;
                }

                if (monto > capitalPendiente) {
                    mostrarError('El monto no puede ser mayor al capital pendiente');
                    return;
                }
            }

            // Mostrar loader
            btnAnalizar.disabled = true;
            btnAnalizar.innerHTML = '<i class="spinner-border spinner-border-sm me-2"></i>Analizando...';

            // Llamar al endpoint de análisis
            fetch(`/emprendimiento/mi-credito/${creditoId}/analizar-abono/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    tipo_abono: tipo,
                    num_cuotas: numCuotas,
                    monto_capital: tipo === 'CAPITAL' ? monto : null
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    datosAnalisis = data;
                    mostrarResultadosAnalisis(data);
                    btnConfirmar.classList.remove('d-none');
                } else {
                    mostrarError(data.error || 'Error al analizar el abono');
                }
            })
            .catch(error => {
                console.error('Error:', error);
                mostrarError('Ocurrió un error inesperado');
            })
            .finally(() => {
                btnAnalizar.disabled = false;
                btnAnalizar.innerHTML = '<i class="bi bi-search me-2"></i>Analizar Impacto';
            });
        });
    }

    function mostrarError(mensaje) {
        document.getElementById('error-mensaje').textContent = mensaje;
        errorAnalisis.classList.remove('d-none');
        resultadosAnalisis.classList.remove('d-none');
        comparacionPlanes.classList.add('d-none');
    }

    function mostrarResultadosAnalisis(data) {
        resultadosAnalisis.classList.remove('d-none');
        comparacionPlanes.classList.remove('d-none');

        // Plan actual
        document.getElementById('plan-actual-cuotas').textContent = data.plan_actual.cuotas_restantes;
        document.getElementById('plan-actual-valor-cuota').textContent = '$' + parseFloat(data.plan_actual.valor_cuota).toLocaleString('es-CO');
        document.getElementById('plan-actual-capital').textContent = '$' + parseFloat(data.plan_actual.capital_pendiente).toLocaleString('es-CO');
        document.getElementById('plan-actual-intereses').textContent = '$' + parseFloat(data.plan_actual.total_intereses).toLocaleString('es-CO');

        // Plan nuevo
        document.getElementById('plan-nuevo-cuotas').textContent = data.plan_nuevo.cuotas_restantes;
        document.getElementById('plan-nuevo-valor-cuota').textContent = '$' + parseFloat(data.plan_nuevo.valor_cuota).toLocaleString('es-CO');
        document.getElementById('plan-nuevo-capital').textContent = '$' + parseFloat(data.plan_nuevo.capital_pendiente).toLocaleString('es-CO');
        document.getElementById('plan-nuevo-intereses').textContent = '$' + parseFloat(data.plan_nuevo.total_intereses).toLocaleString('es-CO');

        // Ahorro
        document.getElementById('ahorro-intereses').textContent = '$' + parseFloat(data.ahorro_intereses).toLocaleString('es-CO');
    }

    // Confirmar pago (abono o pago total)
    if (btnConfirmar) {
        btnConfirmar.addEventListener('click', function() {
            if (tipoAbonoSeleccionado === 'TOTAL') {
                // Pago Total
                if (!datosAnalisis || !datosAnalisis.total_pagar) {
                    alert('Error calculando el pago total. Intenta de nuevo.');
                    return;
                }

                const confirmacion = confirm(
                    `¿Estás seguro de que deseas liquidar tu deuda?\n\n` +
                    `Total a pagar: $${parseFloat(datosAnalisis.total_pagar).toLocaleString('es-CO')}\n` +
                    `Esta acción pagará completamente tu crédito.`
                );

                if (!confirmacion) return;

                // Mostrar loader
                showLoader('Procesando pago total...');
                btnConfirmar.disabled = true;

                // Redirigir a WOMPI con el monto total
                window.location.href = `/emprendimiento/mi-credito/${creditoId}/pago/wompi/?monto=${datosAnalisis.total_pagar}&tipo=TOTAL`;

            } else if (tipoAbonoSeleccionado === 'NORMAL') {
                // Abono Normal
                const montoNormal = parseFloat(montoNormalInput.value || '0');
                const maxCuota = parseFloat(montoNormalInput.dataset.maxCuota || valorCuota || '0');
                const maxSaldo = parseFloat(montoNormalInput.dataset.maxSaldo || saldoPendiente || '0');
                const maxPermitido = Math.min(maxCuota || 0, maxSaldo || 0);

                if (!montoNormal || montoNormal <= 0 || montoNormal > maxPermitido) {
                    alert('El abono debe ser mayor a 0 y menor o igual al valor de la cuota y del saldo pendiente.');
                    return;
                }

                const confirmacion = confirm(
                    `¿Estás seguro de que deseas realizar este abono normal?\n\n` +
                    `Monto: $${montoNormal.toLocaleString('es-CO')}\n` +
                    `La próxima cuota se reducirá en este valor.`
                );

                if (!confirmacion) return;

                // Mostrar loader
                showLoader('Procesando abono...');
                btnConfirmar.disabled = true;

                window.location.href = `/emprendimiento/mi-credito/${creditoId}/pago/wompi/?monto=${montoNormal}&tipo=NORMAL`;

            } else if (tipoAbonoSeleccionado === 'CAPITAL') {
                // Abono a Capital
                if (!datosAnalisis) {
                    alert('Debes analizar el abono primero');
                    return;
                }

                const confirmacion = confirm(
                    `¿Estás seguro de que deseas realizar este abono a capital?\n\n` +
                    `Monto: $${parseFloat(datosAnalisis.monto_abono).toLocaleString('es-CO')}\n` +
                    `Ahorro en intereses: $${parseFloat(datosAnalisis.ahorro_intereses).toLocaleString('es-CO')}\n\n` +
                    `Tu crédito será reestructurado.`
                );

                if (!confirmacion) return;

                // Mostrar loader
                showLoader('Procesando abono...');
                btnConfirmar.disabled = true;

                const montoCapitalConfirm = parseFloat(montoCapitalInput.value);

                // Redirigir a WOMPI con el monto del abono
                window.location.href = `/emprendimiento/mi-credito/${creditoId}/pago/wompi/?monto=${montoCapitalConfirm}&tipo=CAPITAL`;
            }
        });
    }

    // Reset modal al cerrar
    if (abonoModal) {
        abonoModal.addEventListener('hidden.bs.modal', function() {
            tipoAbonoCards.forEach(c => {
                c.classList.remove('border-primary', 'shadow');
                c.style.borderColor = '';
            });
            formCapital.classList.add('d-none');
            formNormal.classList.add('d-none');
            formTotal.classList.add('d-none');
            btnAnalizarContainer.style.display = 'none';
            resultadosAnalisis.classList.add('d-none');
            btnConfirmar.classList.add('d-none');
            errorAnalisis.classList.add('d-none');
            tipoAbonoSeleccionado = null;
            datosAnalisis = null;

            // Reset inputs
            if (montoCapitalInput) montoCapitalInput.value = '';
            if (montoNormalInput) montoNormalInput.value = '';
            if (interesesAcumuladosEl) interesesAcumuladosEl.textContent = '$0';
            if (totalPagarEl) totalPagarEl.textContent = '$0';
            if (capitalPendienteTotalEl) capitalPendienteTotalEl.textContent = '$0';
            if (abonoNormalEl) abonoNormalEl.textContent = '$0';
            if (cuotaRestanteEl) cuotaRestanteEl.textContent = '$0';
            if (normalErrorEl) normalErrorEl.classList.add('d-none');
        });
    }
}
