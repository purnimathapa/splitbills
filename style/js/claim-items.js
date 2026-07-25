(function () {
    var form = document.getElementById('claim-form');
    if (!form || !window.__CLAIM_PREVIEW__) return;

    var totalEl = document.getElementById('claim-live-total');
    var debounceTimer = null;
    var currency = window.__CLAIM_CURRENCY__ || '';
    var rate = parseFloat(window.__CLAIM_RATE__) || 1;

    function selectedIds() {
        return Array.prototype.map.call(
            form.querySelectorAll('.claim-item-cb:checked'),
            function (cb) { return parseInt(cb.value, 10); }
        ).filter(function (n) { return !isNaN(n); });
    }

    function formatAmount(amount) {
        var display = (parseFloat(amount) || 0) * rate;
        return currency + ' ' + display.toFixed(2);
    }

    function refreshTotal() {
        var ids = selectedIds();
        fetch(window.__CLAIM_PREVIEW__, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_ids: ids }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ok && totalEl) {
                    totalEl.textContent = formatAmount(data.total);
                } else if (totalEl) {
                    totalEl.textContent = formatAmount(0);
                }
            })
            .catch(function () {
                if (totalEl) totalEl.textContent = currency + ' —';
            });
    }

    function scheduleRefresh() {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(refreshTotal, 120);
    }

    form.querySelectorAll('.claim-item-cb').forEach(function (cb) {
        cb.addEventListener('change', scheduleRefresh);
    });
    refreshTotal();
})();
