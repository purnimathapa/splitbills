(function () {
    const form = document.getElementById('expense-form');
    if (!form || form.dataset.flow !== 'receipt-guest') return;

    const errEl = document.getElementById('form-error');
    const useItemized = document.getElementById('use_itemized');
    const selfService = document.getElementById('self_service_items');
    const itemizedPanel = document.getElementById('itemized-panel');
    const itemRows = document.getElementById('item-rows');
    const addBtn = document.getElementById('add-item-row');

    if (useItemized) useItemized.checked = true;
    if (selfService) selfService.checked = true;
    if (itemizedPanel) itemizedPanel.hidden = false;

    form.addEventListener('submit', function (e) {
        if (errEl) {
            errEl.hidden = true;
            errEl.textContent = '';
        }

        const desc = document.getElementById('split-description');
        if (!desc || !desc.value.trim()) {
            e.preventDefault();
            showErr('Add a place name, or upload a clearer receipt so we can detect it.');
            return;
        }

        const rows = itemRows ? itemRows.querySelectorAll('.item-row') : [];
        if (!rows.length) {
            e.preventDefault();
            showErr('Scan the receipt or add at least one line item.');
            return;
        }

        let validItems = 0;
        rows.forEach(function (row) {
            const name = row.querySelector('input[name^="item_name_"]');
            const price = row.querySelector('input[name^="item_price_"]');
            if (name && name.value.trim() && price && parseFloat(price.value) > 0) {
                validItems += 1;
            }
        });
        if (validItems === 0) {
            e.preventDefault();
            showErr('Add at least one item with a name and price.');
            return;
        }

        const enabledChecked = form.querySelectorAll(
            'input[name="participant_user_ids"]:enabled:checked'
        );
        if (enabledChecked.length < 1) {
            e.preventDefault();
            showErr('Open “Add people” and select at least one friend for this split.');
            return;
        }

        const btn = document.getElementById('create-split-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Creating…';
        }
    });

    function showErr(msg) {
        if (!errEl) return;
        errEl.textContent = msg;
        errEl.hidden = false;
        if (window.SplitBillsUI && window.SplitBillsUI.toast) {
            window.SplitBillsUI.toast(msg, 'error');
        }
    }
})();
