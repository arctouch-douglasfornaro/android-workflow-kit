---
name: detailed-mode
description: Deactivate concise mode and restore normal, detailed response mode with comprehensive explanations, step-by-step rationale, and architectural context. Use when the user asks for detailed mode, normal responses, verbose mode, full explanations, or says 'modo normal', 'modo detalhado', 'desativar modo conciso', '/detailed', '/normal', '/verbose'.
---

# Detailed Mode (Modo Normal & Detalhado)

Desativa o modo conciso e restaura o comportamento padrão com respostas completas e detalhadas.

Quando ativo, a IA fornece contexto arquitetural, explicações passo a passo, análise de trade-offs e detalhes conceituais.

---

## Gatilhos de Ativação

Ative quando o usuário disser ou digitar:
- `modo normal`, `modo detalhado`, `desativar modo conciso`, `desative o modo direto`, `voltar ao normal`
- `/detailed`, `/normal`, `/verbose`, `/concise off`
- "Explique em detalhes", "pode detalhar", "quero a explicação completa"

---

## Diretrizes de Comportamento (Modo Ativo)

1. **Confirmação Breve**:
   - Confirme a troca de modo em 1 frase rápida (ex: "Modo detalhado restaurado.").

2. **Explicações Completas & Contextualizadas**:
   - Apresente a motivação técnica, boas práticas e impacto na arquitetura.
   - Forneça explicações detalhadas de cada etapa para tarefas complexas.

3. **Análise de Casos de Borda e Alternativas**:
   - Destaque possíveis efeitos colaterais, considerações de performance e casos de teste.

4. **Formatação Equilibrada**:
   - Use títulos, blocos de código formatados, tabelas e listas estruturadas para facilitar a leitura.

---

## Como Reativar o Modo Conciso

Para voltar ao modo rápido e direto ao ponto:
- Diga `modo conciso`, `seja direto`, `/concise`, `/fast` (ativa [`concise-mode`](../concise-mode/SKILL.md)).
