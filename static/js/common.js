/**
 * common.js — shared utilities loaded on every page (F2, F3, F4)
 *
 * Provides:
 *   - Dark mode toggle  (F7)
 *   - Loading overlay   (F3)
 *   - Confirm dialog    (F4)
 *   - Toast helper
 */

/* ── Dark mode (F7) ──────────────────────────────────────────────────────────── */
const DarkMode = (() => {
    const KEY = 'qlda-theme';
    const root = document.documentElement;

    function apply(theme) {
        root.setAttribute('data-theme', theme);
        const btn = document.getElementById('darkModeToggle');
        if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    }

    function init() {
        const saved = localStorage.getItem(KEY) || 'light';
        apply(saved);
        const btn = document.getElementById('darkModeToggle');
        if (btn) {
            btn.addEventListener('click', () => {
                const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
                localStorage.setItem(KEY, next);
                apply(next);
            });
        }
    }

    return { init };
})();


/* ── Loading overlay (F3) ────────────────────────────────────────────────────── */
const Loading = (() => {
    let overlay = null;

    function _getOrCreate() {
        if (overlay) return overlay;
        overlay = document.getElementById('loadingOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'loadingOverlay';
            overlay.innerHTML = `
                <div class="spinner-border" role="status"><span class="visually-hidden">Loading…</span></div>
                <div class="loading-text" id="loadingText">処理中...</div>
            `;
            document.body.appendChild(overlay);
        }
        return overlay;
    }

    function show(text = '処理中...') {
        const el = _getOrCreate();
        const textEl = el.querySelector('#loadingText');
        if (textEl) textEl.textContent = text;
        el.classList.add('active');
    }

    function hide() {
        const el = _getOrCreate();
        el.classList.remove('active');
    }

    return { show, hide };
})();


/* ── Confirm dialog helper (F4) ──────────────────────────────────────────────── */
const Confirm = (() => {
    /**
     * Show a Bootstrap modal confirm dialog.
     * Returns a Promise<boolean>.
     * Falls back to window.confirm() if Bootstrap isn't available.
     *
     * @param {string} message   - Body text
     * @param {string} title     - Modal title (optional)
     * @param {string} okLabel   - OK button label (optional)
     * @param {string} okClass   - OK button Bootstrap class (optional)
     */
    function ask(message, title = '確認', okLabel = 'OK', okClass = 'btn-danger') {
        return new Promise(resolve => {
            // Bootstrap not available → fallback
            if (typeof bootstrap === 'undefined') {
                resolve(window.confirm(message));
                return;
            }

            let el = document.getElementById('_confirmModal');
            if (!el) {
                el = document.createElement('div');
                el.id = '_confirmModal';
                el.className = 'modal fade';
                el.setAttribute('tabindex', '-1');
                el.innerHTML = `
                    <div class="modal-dialog modal-dialog-centered">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h5 class="modal-title" id="_confirmTitle"></h5>
                                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                            </div>
                            <div class="modal-body" id="_confirmBody"></div>
                            <div class="modal-footer">
                                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">キャンセル</button>
                                <button type="button" class="btn" id="_confirmOk"></button>
                            </div>
                        </div>
                    </div>`;
                document.body.appendChild(el);
            }

            el.querySelector('#_confirmTitle').textContent = title;
            el.querySelector('#_confirmBody').textContent  = message;
            const okBtn = el.querySelector('#_confirmOk');
            okBtn.textContent = okLabel;
            okBtn.className   = `btn ${okClass}`;

            const modal = new bootstrap.Modal(el);

            const onOk = () => { modal.hide(); resolve(true);  };
            const onDismiss = () => resolve(false);

            okBtn.onclick = onOk;
            el.addEventListener('hidden.bs.modal', onDismiss, { once: true });

            modal.show();
        });
    }

    return { ask };
})();


/* ── Toast helper ────────────────────────────────────────────────────────────── */
const Toast = (() => {
    function show(message, type = 'success', duration = 3000) {
        const container = (() => {
            let c = document.getElementById('_toastContainer');
            if (!c) {
                c = document.createElement('div');
                c.id = '_toastContainer';
                c.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;z-index:11000;display:flex;flex-direction:column;gap:.5rem;';
                document.body.appendChild(c);
            }
            return c;
        })();

        const icons = { success: '✅', danger: '❌', warning: '⚠️', info: 'ℹ️' };
        const t = document.createElement('div');
        t.className = `alert alert-${type} shadow py-2 px-3 mb-0 d-flex align-items-center gap-2`;
        t.style.cssText = 'min-width:220px;max-width:360px;animation:fadeIn .2s ease;';
        t.innerHTML = `<span>${icons[type] || ''}</span><span>${message}</span>`;
        container.appendChild(t);

        setTimeout(() => {
            t.style.opacity = '0';
            t.style.transition = 'opacity .3s';
            setTimeout(() => t.remove(), 320);
        }, duration);
    }

    return { show };
})();


/* ── Auto-dismiss flash messages ─────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    DarkMode.init();

    setTimeout(() => {
        document.querySelectorAll('.alert-dismissible').forEach(el => {
            el.classList.remove('show');
            setTimeout(() => el.remove(), 300);
        });
    }, 3000);
});
