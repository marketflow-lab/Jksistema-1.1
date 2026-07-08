function encerrarSessao() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_data');
    localStorage.removeItem('permissions');
    navegarComTransicao('/frontend_index.html');
}

/** Verifica se há token na sessão; redireciona se não houver. */
function verificarSessao() {
    if (tokenSessaoExpirado()) {
        redirecionarSessaoExpirada();
        return false;
    }
    if (!obterToken() && !obterClientId()) {
        window.location.href = '/frontend_index.html';
        return false;
    }
    return true;
}
