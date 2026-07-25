(function () {
    const form = document.getElementById('expense-form');
    if (!form) return;

    const radios = form.querySelectorAll('.split-method-radio');
    const useItemized = document.getElementById('use_itemized');
    const selfService = document.getElementById('self_service_items');
    const simplePanel = document.getElementById('simple-split-panel');
    const itemizedPanel = document.getElementById('itemized-panel');
    const groupPanel = document.getElementById('split-group-panel');
    const splitMethod = document.getElementById('split_method');
    const advancedBlock = document.getElementById('advanced-split-options');

    function selectedFlow() {
        const checked = form.querySelector('.split-method-radio:checked');
        return checked ? checked.value : 'split_evenly';
    }

    function applyFlow() {
        const flow = selectedFlow();
        if (groupPanel) {
            groupPanel.hidden = flow !== 'use_group';
        }
        if (advancedBlock) {
            advancedBlock.hidden = flow !== 'split_evenly';
        }

        if (flow === 'pick_items') {
            if (useItemized) {
                useItemized.checked = true;
                useItemized.dispatchEvent(new Event('change'));
            }
            if (selfService) {
                selfService.checked = true;
                selfService.dispatchEvent(new Event('change'));
            }
            if (splitMethod) splitMethod.value = 'equal';
        } else if (flow === 'split_evenly') {
            if (useItemized) {
                useItemized.checked = false;
                useItemized.dispatchEvent(new Event('change'));
            }
            if (selfService) selfService.checked = false;
            if (splitMethod) splitMethod.value = 'equal';
        } else if (flow === 'use_group') {
            if (useItemized) {
                useItemized.checked = false;
                useItemized.dispatchEvent(new Event('change'));
            }
            if (selfService) selfService.checked = false;
            const org = document.getElementById('organize_trip_id');
            if (org && !org.value && org.options.length > 1) {
                org.selectedIndex = 1;
            }
        }
    }

    radios.forEach(function (r) {
        r.addEventListener('change', applyFlow);
    });
    applyFlow();
})();
