(function () {
    'use strict';

    const groups = [...document.querySelectorAll('#panel-processador .file-input-group')];

    function render(group) {
        const file = group.querySelector('input[type="file"]').files[0];
        group.classList.toggle('has-file', Boolean(file));
        group.querySelector('.upload-selection').hidden = !file;
        group.querySelector('.upload-filename').textContent = file ? file.name : '';
    }

    for (const group of groups) {
        const input = group.querySelector('input[type="file"]');
        const feedback = group.querySelector('.upload-feedback');
        const extensions = input.accept.split(',').map(value => value.trim().toLowerCase());
        const accepts = file => extensions.some(extension => file.name.toLowerCase().endsWith(extension));
        const showError = message => {
            feedback.textContent = message;
            feedback.hidden = false;
        };

        input.addEventListener('change', () => {
            feedback.hidden = true;
            feedback.textContent = '';
            if (input.files[0] && !accepts(input.files[0])) {
                input.value = '';
                showError('Selecione um arquivo ' + (extensions.length > 1 ? 'PDF ou XML.' : 'PDF.'));
            }
            render(group);
        });

        group.querySelector('.upload-remove').addEventListener('click', () => {
            input.value = '';
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.focus({ preventScroll: true });
        });

        for (const type of ['dragenter', 'dragover']) {
            group.addEventListener(type, event => {
                event.preventDefault();
                if (input.disabled) return;
                if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
                group.classList.add('is-dragover');
            });
        }
        group.addEventListener('dragleave', event => {
            if (!group.contains(event.relatedTarget)) group.classList.remove('is-dragover');
        });
        group.addEventListener('drop', event => {
            event.preventDefault();
            group.classList.remove('is-dragover');
            if (input.disabled) return;
            const files = event.dataTransfer?.files;
            if (!files || files.length !== 1) {
                showError('Adicione um arquivo por vez neste campo.');
                return;
            }
            if (!accepts(files[0])) {
                showError('Selecione um arquivo ' + (extensions.length > 1 ? 'PDF ou XML.' : 'PDF.'));
                return;
            }
            const transfer = new DataTransfer();
            transfer.items.add(files[0]);
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        render(group);
    }

    // A troca para Amazon limpa o arquivo principal no fluxo existente.
    document.querySelectorAll('input[name="loja"]').forEach(radio => {
        radio.addEventListener('change', () => {
            for (const group of groups) {
                group.classList.remove('is-dragover');
                group.querySelector('.upload-feedback').hidden = true;
                render(group);
            }
        });
    });
}());
