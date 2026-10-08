/* Expansão compacta do painel da conta; sem chamadas de dados ou atualização. */
(function controlarPainelUsuarioCompacto() {
    'use strict';
    const sidebar = document.querySelector('.user-sidebar');
    const toggle = document.getElementById('user-sidebar-toggle');
    if (!sidebar || !toggle) return;
    const telaCompacta = window.matchMedia('(max-width: 720px)');
    function definirExpandido(expandido) {
        sidebar.classList.toggle('user-sidebar-expanded', expandido);
        toggle.setAttribute('aria-expanded', String(expandido));
    }
    toggle.addEventListener('click', () => {
        definirExpandido(toggle.getAttribute('aria-expanded') !== 'true');
    });
    sidebar.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || toggle.getAttribute('aria-expanded') !== 'true') return;
        definirExpandido(false);
        toggle.focus();
    });
    telaCompacta.addEventListener('change', () => {
        if (!document.body.classList.contains('modo-mobile')) definirExpandido(false);
    });
})();
