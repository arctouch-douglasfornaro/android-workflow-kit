# Breakdown

Ajude a pessoa a entender e aplicar **breakdown**: decompor um objetivo em partes menores que formem um caminho claro até um resultado verificável. O propósito não é produzir a maior lista de tarefas possível; é tornar o objetivo executável e fácil de acompanhar.

## Escolha a forma de ajudar

- **Aprender o conceito:** explique em linguagem simples, com um exemplo concreto. Mostre como cada parte contribui para o objetivo. Não transforme uma pergunta conceitual em um plano de projeto completo.
- **Decompor um objetivo:** monte um breakdown para o objetivo que a pessoa trouxe. Se for uma mudança em software existente e o repositório estiver disponível, investigue o código relevante antes de propor o plano. Se ela também quer aprender, explique por que escolheu esses agrupamentos e essa ordem.
- **Fazer os dois:** ensine o conceito usando o próprio objetivo da pessoa como exemplo.

Responda no idioma que a pessoa está usando. Explique termos como *entrega*, *dependência*, *fatia vertical* ou *critério de aceite* quando forem úteis; não pressuponha familiaridade com jargão.

## Modelo mental

Use a menor cadeia que ajude a pensar no caso:

```text
Objetivo → resultados ou entregas → trabalho necessário → evidência de conclusão
```

- **Objetivo:** a mudança ou resultado que se quer alcançar.
- **Resultado/entrega:** uma parte coerente que aproxima alguém desse objetivo.
- **Trabalho:** ações concretas para produzir esse resultado.
- **Evidência de conclusão:** como reconhecer que a parte e o objetivo foram alcançados.

Nem todo caso precisa de todos esses níveis. Um breakdown é um mapa de trabalho que pode ser ajustado quando surgem informações novas; não é uma promessa de que tudo foi previsto desde o início.

## Como decompor

1. **Esclareça o resultado desejado.** Reformule o objetivo como algo observável quando isso remover ambiguidade. Preserve a intenção original.
2. **Defina o que significa sucesso.** Use condições verificáveis, limites e restrições já fornecidos. Não invente prazo, escopo ou requisito como se fosse fato.
3. **Escolha cortes que façam sentido para o objetivo.** Para app ou feature, comece pelas tarefas que a pessoa usuária precisa concluir e por resultados completos. Para outros projetos, agrupe por entregas, fases ou capacidades naturais do trabalho.
4. **Divida as partes grandes.** Continue até que cada item menor descreva uma ação ou resultado claro, possa ser acompanhado e tenha um jeito razoável de verificar conclusão.
5. **Mostre relações entre as partes.** Indique o que precisa vir antes, o que pode ocorrer em paralelo e o que ainda depende de uma decisão.
6. **Revise o tamanho e o escopo.** Junte passos minúsculos que não precisam ser acompanhados separadamente. Divida itens vagos, grandes demais ou que escondam resultados distintos. Separe o necessário do que pode ficar para depois.
7. **Explique a lógica.** Quando a pessoa estiver aprendendo, diga por que os itens foram agrupados e o que conecta cada parte ao objetivo.

Para software, evite dividir automaticamente em “frontend, backend e testes” quando isso não mostrar valor entregue. Prefira fatias completas de comportamento — cada fatia pode atravessar as camadas técnicas necessárias. Organizar tarefas por camada continua útil quando há uma dependência técnica real ou quando esse nível de detalhe foi solicitado.

### Quando o objetivo envolve um código existente

Antes de fechar o breakdown, investigue a parte relevante do repositório para entender o comportamento e as restrições atuais. Leia [a referência de investigação de código](references/codebase-investigation.md) quando a tarefa envolver um app, serviço, biblioteca ou outro projeto de software existente.

Use o código como evidência de **como o sistema funciona hoje**, não como prova do comportamento que o produto deveria ter. Separe fatos observados, interpretações e escolhas de produto ainda abertas. Faça primeiro as perguntas que o código não consegue responder; não pergunte ao usuário algo que possa ser confirmado lendo a implementação, seus callers ou testes.

O breakdown final deve ligar cada fatia de resultado às áreas técnicas comprovadamente envolvidas e indicar como verificar sua conclusão. Cite arquivos e linhas para sustentar fatos importantes. Nesta etapa, explore o código em modo de leitura; não altere arquivos nem rode builds ou testes a menos que isso seja pedido separadamente.

Se o repositório não estiver acessível, diga que a decomposição não está fundamentada no código e peça acesso ou ofereça um breakdown conceitual claramente marcado como provisório.

## Granularidade útil

Um item provavelmente ainda está grande se:

- contém vários resultados diferentes;
- não está claro o que significa terminá-lo;
- depende de uma decisão ou investigação que não aparece no breakdown.

Um item provavelmente está pequeno demais se:

- é apenas um gesto operacional sem valor de acompanhamento próprio;
- criar, atribuir e marcar como concluído cada microetapa gera mais trabalho do que visibilidade.

Não atribua pessoas, estimativas ou datas sem contexto ou pedido. Não invente tarefas no nível de arquivos, telas ou serviços sem evidência do projeto.

## Como apresentar

Adapte o formato ao pedido. Para uma explicação conceitual, uma definição curta, um exemplo e a lógica do exemplo costumam bastar. Para um objetivo concreto, use quando útil:

```text
Objetivo:
Sucesso significa:
Breakdown:
1. Resultado ou fatia
   - Trabalho necessário
   - Como saber que terminou
2. Resultado ou fatia
   - Trabalho necessário
   - Como saber que terminou
Ordem e dependências:
Premissas ou decisões em aberto:
Fora do escopo por enquanto:
```

Inclua apenas seções que ajudem aquele caso. Pergunte somente quando uma resposta mudaria materialmente o breakdown; se der para avançar, declare uma premissa simples e siga.

## Exemplo

Objetivo: **a pessoa consegue salvar sets para estudar depois.**

```text
Objetivo
└── Guardar e reencontrar sets para estudar depois
    ├── Salvar ou remover um set a partir do set
    │   └── Concluído quando a ação muda o estado do set e esse estado é preservado
    └── Encontrar os sets salvos na biblioteca
        └── Concluído quando a lista mostra os sets guardados e um estado vazio compreensível
```

Esse corte acompanha dois resultados percebidos pela pessoa: guardar um set e encontrá-lo depois. A implementação pode exigir interface, persistência e testes em ambos; essas camadas são trabalho para realizar cada resultado, não necessariamente entregas independentes. As condições exatas devem ser confirmadas pelo contexto do produto.
