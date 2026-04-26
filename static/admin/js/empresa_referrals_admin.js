(function () {
    function toggleReferralFields() {
        const referredCheckbox = document.getElementById('id_fue_referida');
        const advisorSelect = document.getElementById('id_asesor_comercial');
        const nameRow = document.querySelector('.form-row.field-asesor_nombre, .field-asesor_nombre');
        const cedulaRow = document.querySelector('.form-row.field-asesor_cedula, .field-asesor_cedula');
        const advisorRow = document.querySelector('.form-row.field-asesor_comercial, .field-asesor_comercial');
        if (!referredCheckbox) {
            return;
        }
        const visible = referredCheckbox.checked;
        const useManualCapture = visible && (!advisorSelect || !advisorSelect.value);
        [advisorRow].forEach((row) => {
            if (!row) {
                return;
            }
            row.style.display = visible ? '' : 'none';
        });
        [nameRow, cedulaRow].forEach((row) => {
            if (!row) {
                return;
            }
            row.style.display = useManualCapture ? '' : 'none';
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        const referredCheckbox = document.getElementById('id_fue_referida');
        if (!referredCheckbox) {
            return;
        }
        toggleReferralFields();
        referredCheckbox.addEventListener('change', toggleReferralFields);
        const advisorSelect = document.getElementById('id_asesor_comercial');
        if (advisorSelect) {
            advisorSelect.addEventListener('change', toggleReferralFields);
        }
    });
})();
