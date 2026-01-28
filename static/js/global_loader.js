(function () {
    const globalLoader = document.getElementById('globalLoader');
    if (!globalLoader) {
        return;
    }

    const loaderText = globalLoader.querySelector('.loader-text');
    const loaderContainer = globalLoader.querySelector('#lottieLoader');
    const defaultAnimation = globalLoader.dataset.animation;
    const successAnimation = globalLoader.dataset.successAnimation;
    let currentPath = null;
    let lottieAnimation = null;

    function loadAnimation(path, loop) {
        if (!loaderContainer || !window.lottie || !path) {
            return;
        }
        if (currentPath === path && lottieAnimation) {
            return;
        }
        if (lottieAnimation) {
            lottieAnimation.destroy();
        }
        loaderContainer.innerHTML = '';
        lottieAnimation = lottie.loadAnimation({
            container: loaderContainer,
            renderer: 'svg',
            loop: loop,
            autoplay: true,
            path: path
        });
        currentPath = path;
    }

    function getAnimationPath(type) {
        if (type === 'check' && successAnimation) {
            return successAnimation;
        }
        return defaultAnimation;
    }

    let skipNextUnload = false;
    let showTimer = null;
    let hideTimer = null;
    let activeSince = 0;
    let pendingText = 'Cargando...';
    let pendingType = 'loading';
    const SHOW_DELAY_MS = 150;
    const MIN_VISIBLE_MS = 350;

    function performShow(text, type) {
        const path = getAnimationPath(type);
        const loop = type !== 'check';
        loadAnimation(path, loop);
        if (loaderText && text) {
            loaderText.textContent = text;
        }
        globalLoader.classList.add('active');
        activeSince = Date.now();
    }

    function showLoader(text, type = 'loading', options = {}) {
        const immediate = options && options.immediate === true;
        pendingText = text || pendingText;
        pendingType = type || pendingType;

        if (hideTimer) {
            clearTimeout(hideTimer);
            hideTimer = null;
        }

        if (immediate) {
            if (showTimer) {
                clearTimeout(showTimer);
                showTimer = null;
            }
            performShow(pendingText, pendingType);
            return;
        }

        if (globalLoader.classList.contains('active')) {
            performShow(pendingText, pendingType);
            return;
        }

        if (showTimer) {
            return;
        }

        showTimer = setTimeout(function () {
            showTimer = null;
            performShow(pendingText, pendingType);
        }, SHOW_DELAY_MS);
    }

    function hideLoader() {
        if (showTimer) {
            clearTimeout(showTimer);
            showTimer = null;
        }
        if (!globalLoader.classList.contains('active')) {
            return;
        }
        const elapsed = Date.now() - activeSince;
        if (elapsed < MIN_VISIBLE_MS) {
            if (hideTimer) {
                clearTimeout(hideTimer);
            }
            hideTimer = setTimeout(function () {
                hideTimer = null;
                globalLoader.classList.remove('active');
            }, MIN_VISIBLE_MS - elapsed);
            return;
        }
        globalLoader.classList.remove('active');
    }

    window.AprobadoLoader = {
        show: showLoader,
        hide: hideLoader
    };

    window.addEventListener('beforeunload', function () {
        if (skipNextUnload) {
            skipNextUnload = false;
            return;
        }
        showLoader('Cargando...', 'loading', { immediate: true });
    });

    window.addEventListener('load', function () {
        hideLoader();
    });

    window.addEventListener('pageshow', function (event) {
        if (event && event.persisted) {
            if (showTimer) {
                clearTimeout(showTimer);
                showTimer = null;
            }
            if (hideTimer) {
                clearTimeout(hideTimer);
                hideTimer = null;
            }
            globalLoader.classList.remove('active');
        }
    });

    document.addEventListener('click', function (event) {
        const link = event.target.closest('a');
        if (!link || !link.href) {
            return;
        }
        if (link.dataset.loader === 'off' || link.dataset.download === 'true' || link.hasAttribute('download')) {
            skipNextUnload = true;
            setTimeout(function () {
                skipNextUnload = false;
            }, 1000);
            return;
        }
        if (link.target === '_blank' || link.href.includes('#') || link.href.includes('javascript:')) {
            return;
        }
        showLoader('Cargando...');
    });

    document.addEventListener('submit', function (event) {
        const form = event.target;
        if (!form || form.dataset.loader === 'off' || form.dataset.loader === 'ajax') {
            return;
        }
        const submitter = event.submitter;
        const loaderType = (submitter && submitter.dataset.loaderType) || form.dataset.loaderType || 'loading';
        const loaderTextValue = (submitter && submitter.dataset.loaderText) || form.dataset.loaderText || 'Procesando...';
        showLoader(loaderTextValue, loaderType);
    });
})();
