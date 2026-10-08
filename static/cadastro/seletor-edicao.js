(function (global) {
    'use strict';

    const storeTools = global.JKCadastroStore;
    if (!storeTools) throw new Error('Escopo de loja do Cadastro não inicializado.');
    global.location.replace(storeTools.urlPagina('/cadastro.html', storeTools.storeIdDaUrl().valor));
})(window);
