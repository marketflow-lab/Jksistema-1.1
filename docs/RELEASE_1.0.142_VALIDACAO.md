# Validação da versão 1.0.142

A versão parte da release 1.0.141 e incorpora somente a correção aprovada `0efaacaa806fe93aa0488bc548f49f9559b15da9`, equivalente ao commit `efeb28516d6f8308a77f73e0e59a9aed9ed84340` integrado ao desenvolvimento.

- 96 testes Python de promoções e 83 subtestes aprovados.
- 6 testes dos contratos de versão e distribuição aprovados.
- 3 verificações de interface aprovadas: margem no navegador, seleção manual e resultados de participação.
- Simulação de atualização empacotada de 1.0.99 para 1.0.142 aprovada, com preservação dos dados simulados.
- Os três arquivos corrigidos do aplicativo conferem com o conteúdo empacotado.
- Locks de dependências, 24 espelhos HTML, manifesto editorial, compilação Python e diff verificados.
- Recursos de distribuição conferidos antes e depois do empacotamento; 1.046 arquivos gerenciados no manifesto local.

O runtime e seus hashes são os mesmos da versão 1.0.141, já submetida à instalação offline isolada. A instalação offline profunda e a suíte completa não foram repetidas: esta validação cobre a alteração restrita em promoções e o pacote atualizado.

Permanecem as duas pendências preexistentes registradas no relatório da 1.0.141: orçamento de tamanho do módulo Cadastro e instabilidade da identidade do WhatsApp quando o armazenamento seguro está indisponível. Esta release não altera esses componentes.

Nenhuma atualização do aplicativo instalado ou mutação em serviços e dados de lojas foi executada durante a validação.
