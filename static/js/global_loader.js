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

    function showLoader(text, type = 'loading') {
        const path = getAnimationPath(type);
        const loop = type !== 'check';
        loadAnimation(path, loop);
        if (loaderText && text) {
            loaderText.textContent = text;
        }
        globalLoader.classList.add('active');
    }

    function hideLoader() {
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
        showLoader('Cargando...');
    });

    window.addEventListener('load', function () {
        hideLoader();
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
