const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jkPrivateBootstrap', {
    submit(password) {
        ipcRenderer.send('jk-private-bootstrap-submit', String(password || ''));
    },
    cancel() {
        ipcRenderer.send('jk-private-bootstrap-cancel');
    }
});

window.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('unlock-form');
    const input = document.getElementById('password');
    const error = document.getElementById('error');
    const cancel = document.getElementById('cancel');
    const unlock = document.getElementById('unlock');

    const setBusy = (busy) => {
        input.disabled = busy;
        cancel.disabled = busy;
        unlock.disabled = busy;
    };

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        error.textContent = '';
        const password = String(input.value || '');
        if (password.length < 16) {
            error.textContent = 'A senha precisa ter pelo menos 16 caracteres.';
            return;
        }
        setBusy(true);
        window.jkPrivateBootstrap.submit(password);
        input.value = '';
    });

    cancel.addEventListener('click', () => window.jkPrivateBootstrap.cancel());
    ipcRenderer.on('jk-private-bootstrap-error', (_event, message) => {
        setBusy(false);
        error.textContent = String(message || 'Nao foi possivel desbloquear o cofre privado.');
        input.focus();
    });
});
