(function () {
    const THEME_KEY = 'splitbills-theme';

    function toast(message, type) {
        if (typeof Toastify === 'undefined') return;
        const styles = {
            success: 'linear-gradient(135deg, #059669, #047857)',
            error: 'linear-gradient(135deg, #dc2626, #b91c1c)',
            info: 'linear-gradient(135deg, #2563eb, #1d4ed8)',
            warning: 'linear-gradient(135deg, #d97706, #b45309)',
        };
        const isMobile = window.matchMedia('(max-width: 480px)').matches;
        Toastify({
            text: message,
            duration: 4200,
            gravity: 'top',
            position: isMobile ? 'center' : 'right',
            stopOnFocus: true,
            style: {
                background: styles[type] || styles.info,
                borderRadius: '10px',
                fontSize: '0.9rem',
                maxWidth: isMobile ? '92vw' : '360px',
            },
        }).showToast();
    }

    function flashCategory(raw) {
        if (raw === 'error') return 'error';
        if (raw === 'info') return 'info';
        if (raw === 'warning') return 'warning';
        return 'success';
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(THEME_KEY, theme);
    }

    function initTheme() {
        const saved = localStorage.getItem(THEME_KEY);
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        applyTheme(saved || (prefersDark ? 'dark' : 'light'));
        document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
                applyTheme(next);
            });
        });
    }

    function showFlashToasts() {
        const node = document.getElementById('flash-messages-json');
        if (!node) return;
        try {
            const messages = JSON.parse(node.textContent || '[]');
            messages.forEach(function (entry) {
                toast(entry[1], flashCategory(entry[0]));
            });
        } catch (e) { /* ignore */ }
    }

    function initModal(modalId) {
        const modal = document.getElementById(modalId);
        if (!modal) return;
        const openers = document.querySelectorAll('[data-open-modal="' + modalId + '"]');
        const closeEls = modal.querySelectorAll('[data-close-modal]');

        function open() {
            modal.hidden = false;
            requestAnimationFrame(function () { modal.classList.add('is-open'); });
            document.body.classList.add('modal-open');
        }
        function close() {
            modal.classList.remove('is-open');
            document.body.classList.remove('modal-open');
            setTimeout(function () { modal.hidden = true; }, 220);
        }

        openers.forEach(function (el) { el.addEventListener('click', open); });
        closeEls.forEach(function (el) { el.addEventListener('click', close); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && modal.classList.contains('is-open')) close();
        });
    }

    function initSheet(sheetId) {
        const sheet = document.getElementById(sheetId);
        if (!sheet) return;
        const openers = document.querySelectorAll('[data-open-sheet="' + sheetId + '"]');
        const closeEls = sheet.querySelectorAll('[data-close-sheet]');

        function open() {
            sheet.hidden = false;
            requestAnimationFrame(function () { sheet.classList.add('is-open'); });
            document.body.classList.add('modal-open');
        }
        function close() {
            sheet.classList.remove('is-open');
            document.body.classList.remove('modal-open');
            setTimeout(function () { sheet.hidden = true; }, 280);
        }

        openers.forEach(function (el) { el.addEventListener('click', open); });
        closeEls.forEach(function (el) { el.addEventListener('click', close); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && sheet.classList.contains('is-open')) close();
        });
    }

    function initCopyPayLinks() {
        document.querySelectorAll('.copy-pay-link').forEach(function (button) {
            button.addEventListener('click', function () {
                const url = button.getAttribute('data-url');
                if (!url) return;
                navigator.clipboard.writeText(url).then(function () {
                    const label = button.getAttribute('data-copy-label') || 'Link copied';
                    toast(label, 'success');
                });
            });
        });
    }

    function initPaySubmitLoading() {
        document.querySelectorAll('.pay-page form').forEach(function (form) {
            form.addEventListener('submit', function () {
                const btn = form.querySelector('button[type="submit"]');
                if (!btn || btn.disabled) return;
                btn.disabled = true;
                btn.dataset.originalText = btn.textContent;
                btn.textContent = 'Processing…';
                btn.classList.add('is-loading');
            });
        });
    }

    function initChartSkeletons() {
        document.querySelectorAll('[data-chart-skeleton]').forEach(function (wrap) {
            const canvas = wrap.querySelector('canvas');
            if (!canvas) {
                wrap.classList.remove('is-loading');
                return;
            }
            wrap.classList.add('is-loading');
            requestAnimationFrame(function () {
                setTimeout(function () {
                    wrap.classList.remove('is-loading');
                }, 120);
            });
        });
        window.addEventListener('load', function () {
            document.querySelectorAll('[data-chart-skeleton].is-loading').forEach(function (el) {
                el.classList.remove('is-loading');
            });
        });
    }

    function initFeedShell() {
        document.querySelectorAll('[data-feed-shell]').forEach(function (shell) {
            shell.classList.add('is-loading');
            requestAnimationFrame(function () {
                shell.classList.remove('is-loading');
            });
        });
    }

    function initNotificationBell() {
        document.querySelectorAll('[data-notif-bell]').forEach(function (root) {
            const toggle = root.querySelector('.notif-bell__toggle');
            const panel = root.querySelector('.notif-panel');
            if (!toggle || !panel) return;

            function close() {
                panel.hidden = true;
                toggle.setAttribute('aria-expanded', 'false');
            }
            function open() {
                panel.hidden = false;
                toggle.setAttribute('aria-expanded', 'true');
            }

            toggle.addEventListener('click', function (e) {
                e.stopPropagation();
                if (panel.hidden) open();
                else close();
            });
            document.addEventListener('click', function (e) {
                if (!root.contains(e.target)) close();
            });
        });
    }

    window.SplitBillsUI = { toast: toast, initSheet: initSheet };

    document.addEventListener('DOMContentLoaded', function () {
        initTheme();
        showFlashToasts();
        initSheet('add-expense-sheet');
        initModal('quick-expense-modal');
        initCopyPayLinks();
        initPaySubmitLoading();
        initChartSkeletons();
        initFeedShell();
        initNotificationBell();

        if (document.body.dataset.toastSuccess) {
            toast(document.body.dataset.toastSuccess, 'success');
        }
        if (document.body.dataset.toastError) {
            toast(document.body.dataset.toastError, 'error');
        }
        if (document.body.dataset.toastInfo) {
            toast(document.body.dataset.toastInfo, 'info');
        }
    });
})();
