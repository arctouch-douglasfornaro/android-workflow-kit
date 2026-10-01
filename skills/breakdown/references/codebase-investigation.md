# Investigar o código antes de decompor uma mudança

Use este roteiro somente quando o objetivo for mudar um projeto de software existente. A meta é entender profundamente a fatia de código que pode ser afetada, não ler o repositório inteiro.

## Trace o comportamento real

1. Localize a raiz do projeto, as instruções locais (`AGENTS.md` e equivalentes) e a documentação de arquitetura necessária para entender limites de módulos e convenções relevantes.
2. Transforme a meta em termos pesquisáveis: nome da tela, ação, texto, evento, rota, modelo, endpoint ou regra mencionada. Pesquise esses termos e siga as referências para encontrar o ponto de entrada real.
3. Leia a implementação ponta a ponta: entrada do usuário ou evento → controlador/handler/ViewModel → regra ou caso de uso → repositório/serviço/fonte local ou remota → estado/modelo apresentado. Siga também o caminho de volta quando ele muda o estado ou navega.
4. Verifique callers, consumidores e caminhos alternativos próximos. Identifique quem é dono de cada estado ou invariável e se a mudança atravessa módulos, clientes, processos ou contratos.
5. Leia testes e fixtures relacionados, mais um ou dois exemplos vizinhos quando necessário, para identificar comportamento existente, convenções e lacunas de cobertura. Testes ajudam a entender o comportamento codificado; não provam sozinhos que esse comportamento é o requisito correto.

Use busca e navegação de código disponíveis no ambiente (por exemplo, pesquisa textual e referências de símbolos). Siga os links e chamadas encontrados; não conclua pelo primeiro resultado de busca nem examine diretórios sem relação com a meta.

## Examine os riscos que o fluxo realmente toca

Conforme a área afetada, verifique aspectos como persistência e migração, escopo por conta/usuário, cache, estado assíncrono e ciclo de vida, concorrência, navegação, permissões, autenticação, contratos de API, flags, analytics, localização, compatibilidade e estados de erro/vazio/carregamento. Escolha os que se aplicam à mudança; não despeje uma checklist genérica no resultado.

Procure um padrão existente no projeto que resolva um caso comparável e confirme se ele é realmente usado pelos callers. Não presuma que uma abstração, comentário antigo ou classe com nome semelhante ainda define o caminho ativo.

## Saiba quando há evidência suficiente

Continue a investigação até conseguir responder, com referências concretas:

- Onde começa o fluxo relevante e por quais camadas ele passa?
- Quem possui o estado, dados ou regra que a meta precisa alterar?
- Quais callers, consumidores ou contratos podem ser afetados?
- Que comportamento já existe e quais testes o registram?
- Que convenções e limites do projeto condicionam a solução?

Pare quando essas respostas estiverem claras para a fatia relevante. Se uma resposta continuar incerta, registre o que faltou e por que importa; não compense falta de evidência lendo o repositório indiscriminadamente.

## Converta a investigação em perguntas e trabalho

- Resolva pelo código as dúvidas técnicas que ele pode responder. Reserve perguntas ao usuário para decisões de produto, regras de negócio, compatibilidade, rollout, prioridade ou escopo que não estejam especificadas.
- Para cada pergunta importante, resuma o comportamento atual com caminho e linha e explique qual decisão do breakdown depende da resposta. Agrupe perguntas realmente bloqueadoras e avance com premissas explícitas nas demais.
- Separe no relatório **observado no código**, **inferido** e **decisão ainda aberta**. Não apresente uma inferência como requisito confirmado.
- Ao listar cada fatia, descreva o resultado esperado, as áreas/camadas envolvidas conforme o código encontrado, a dependência relevante e a evidência que demonstraria conclusão. Cite caminhos e números de linha exatos que foram lidos; não estime localizações.
- Use testes existentes para derivar caminhos de verificação e aponte lacunas relevantes. Não crie testes, edite código, rode builds ou declare aprovação de implementação: o objetivo aqui é produzir um breakdown fundamentado.

## Formato sugerido para uma mudança em software

Adapte à complexidade; omita seções sem valor.

```text
Objetivo e sucesso esperado:
Fluxo atual observado no código:
  - fatos com caminho:linha
Breakdown:
  1. resultado/fatia, áreas afetadas, dependências, como verificar
Perguntas de produto ou decisões abertas:
Premissas e riscos relevantes:
```
