---
name: rtk
description: Ativa a otimização e compressão de comandos de terminal via RTK (Rust Token Killer) para a sessão atual. Use quando o usuário digitar /rtk ou /rtk-mode.
---

# RTK Mode (Rust Token Killer)

Ativa a interceptação e compressão de saídas de comandos de terminal (Bash) através do proxy nativo **RTK**.

---

## Gatilhos de Ativação

Ative **apenas** quando o usuário executar explicitamente os comandos:
- `/rtk`, `/rtk-mode`, `/rtk on`

---

## O que o Modo Faz

1. **Otimização de Comandos de Terminal**:
   - Os comandos executados no terminal (ex: `git status`, `git diff`, `pytest`, `cargo test`, `npm test`, `ls`, `grep`) são interceptados e comprimidos pelo proxy nativo `rtk`, reduzindo em até 90% o volume de saída que entra na janela de contexto.
2. **Uso Direto de Ferramentas RTK**:
   - Quando este modo estiver ativo, você também pode optar por usar diretamente as ferramentas de leitura do RTK no terminal para arquivos grandes (ex.: `rtk read <caminho>`, `rtk grep <padrão>`, `rtk ls <diretório>`).
3. **Escopo da Sessão**:
   - O RTK permanece ativo apenas durante a **sessão atual**. Ao iniciar uma nova sessão, ele volta ao estado inativo por padrão.

---

## Como Desativar

Quando o usuário pedir: `/rtk off`, `/rtk-off`, `desativar rtk`, `parar rtk`:
- O hook de interceptação é desativado para a sessão atual.
- Os comandos de terminal voltam ao comportamento padrão sem compressão.
