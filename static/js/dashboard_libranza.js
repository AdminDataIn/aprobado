// ===== LOADER GLOBAL =====
const globalLoader = document.getElementById('globalLoader');
let lottieAnimation;

function showLoader(text = 'Cargando...') {
    if (!globalLoader) return;
    const loaderText = document.querySelector('.loader-text');
    if (loaderText) loaderText.textContent = text;
    globalLoader.classList.add('active');
}

function hideLoader() {
    if (!globalLoader) return;
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

function initLottieLoader(path) {
    const loaderContainer = document.getElementById('lottieLoader');
    if (!loaderContainer || !window.lottie || !path) return;
    lottieAnimation = lottie.loadAnimation({
        container: loaderContainer,
        renderer: 'svg',
        loop: true,
        autoplay: true,
        path: path
    });
}

window.addEventListener('beforeunload', function() {
    showLoader('Cargando...');
});

window.addEventListener('load', function() {
    hideLoader();
});

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

function submitWithLoader(form, loaderText = 'Procesando...') {
    showLoader(loaderText);
    form.submit();
}

function cambiarCredito(creditoId) {
    if (creditoId) {
        window.location.href = `/libranza/mi-credito/${creditoId}/`;
    }
}
