// Debug: Verificar sessão no início
if (typeof verificarSessao === 'function' && !verificarSessao()) {
    // Redirecionamento já tratado por auth.js
}
const userData = localStorage.getItem('user_data');
console.log('userData no localStorage:', userData);
if (!userData) {
    console.warn('userData não encontrado no localStorage');
}
