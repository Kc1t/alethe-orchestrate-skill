# Alethe Orchestrate: arquitetura e funcionalidades

Documento de análise do repositório: skill versão 21 e Alethe Bar versão 0.1.0. As alterações locais do widget ainda precisam de validação funcional antes de serem consideradas prontas para distribuição.

## 1. O que é

Uma **Agent Skill** (`SKILL.md`) e plugin para Claude Code e Codex. Um agente "líder" planeja o trabalho e entrega
tarefas limitadas para outros agentes de CLI (**Claude Code, Codex, Antigravity, Kimi** ou qualquer CLI),
seguindo *playbooks* que economizam contexto e cota. No fim, gera um relatório com tokens e custo reais.

O runner usa **Python stdlib** (3.9+) para chamar CLIs como subprocessos, mais duas páginas HTML offline. Sem Python,
o líder pode seguir o fluxo manual da skill. O widget é um aplicativo separado, com dependências Tauri, e consulta o GitHub para verificar atualizações.

## 2. Estrutura do repositório

```text
.claude-plugin/ .codex-plugin/ .agents/    manifestos de plugin e marketplace
hooks/hooks.json                            hook UserPromptSubmit -> orchestrate.py hook
skills/alethe/
├── SKILL.md                                instruções que o agente líder lê
├── playbooks.yaml                          11 playbooks (estratégias) embutidos
├── prices.json                             preços por modelo (custo/forecast)
├── schemas/plan.schema.json                contrato do plano de execução
├── scripts/orchestrate.py                  runner (~5.800 linhas, único módulo de lógica)
├── scripts/diagram.py                      gera diagramas do plano
├── assets/editor.html, report.html         editor de playbooks e relatório (HTML offline)
├── assets/brands/                          logos dos provedores
└── references/*.md                         manuais carregados sob demanda (routing, providers, ...)
tools/check_playbooks.py                    validador de playbooks (CI)
tests/                                      pytest + CLIs falsas (fake_cli.py etc.)
docs/assets/                                imagens e desenhos do README
widget/                                     barra Windows (Tauri 2, Rust e HTML)
```

## 3. Arquitetura

```text
Usuário ──/alethe──▶ Agente líder (Claude Code / Codex)
                       │ lê SKILL.md + playbook escolhido
                       │ escreve plan.json (workers, briefs, modos, budgets)
                       ▼
              orchestrate.py  (validate → dry-run → run)
                       │ ThreadPool, até 16 workers, timeout 1200 s
        ┌──────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼              ▼
     claude         codex            agy            kimi / custom
   (plan/accept)  (sandbox)        (plan/accept)   (contrato + git check)
        │  cada worker: git worktree próprio se escreve
        ▼
   disco: state.json, outcome.json, result.md, diff.patch, stdout/stderr.log
                       ▲
 Líder lê só result.md ┘ → mark used/partial/discarded → report
```

### Camadas do `orchestrate.py`

| Camada | Responsabilidade |
|---|---|
| **Playbooks** | Parser YAML próprio (sem PyYAML), validação estrutural, cache, merge do playbook do usuário (`alethe-playbooks.yaml`) sobre o embutido |
| **Policy** | `alethe-orchestrate.json`: preferências e limites; tiers e perfis ficam nos playbooks (um `modelTiers` legado na policy ainda prevalece) |
| **Plano** | `validate_plan`: IDs, providers, estratégia, limites, paths relativos, prompts; `dry-run` mostra o que rodaria sem executar |
| **Execução** | `run_batch` / `run_worker`: monta o comando de cada CLI, aplica modo leitura vs escrita, worktrees git, timeout, job object no Windows, parada via arquivo `stop` |
| **Salvaguardas** | Orçamento por wave e por orquestração, teto de custo nos workers Claude, bloqueio de flags inseguras, tripwire (git detecta worker "read-only" que escreveu), sem subagentes aninhados |
| **Cota** | Lê uso de Claude e Codex antes de cada wave, prevê consumo, recusa iniciar passando da linha de parada |
| **Observabilidade** | `status`, `wait`, `report` (md/json/html), `journal.jsonl` com cada mudança de rota e seu motivo |
| **Modos / hook** | `on` expira após 12 h sem uso; `always` vale em todos os projetos sem expiração. O hook `UserPromptSubmit` lembra o objetivo e as regras no Claude Code |
| **Editor** | `editor.html` autocontido: edita playbooks, simula cota, copia o YAML de volta |

### Modelo de dados em disco

Base: `%APPDATA%\Alethe\` (Windows) ou `~/.config/alethe/`; sobrescrevível por `ALETHE_ORCHESTRATE_POLICY` e
`ALETHE_ORCHESTRATE_HOME`.

```text
orchestrations/
├── current.json                 {folder, updatedAt}  → orquestração ativa
└── <orquestração>/
    ├── orchestration.json, journal.jsonl, budget.json
    └── 01-explore/
        ├── state.json           status do batch e de cada worker, ao vivo
        ├── plan.json
        └── workers/<id>/        prompt.txt, result.md, outcome.json, diff.patch, mark.json, logs
```

`state.json` é escrito de forma atômica (`atomic_write_json`) e é a **interface natural para qualquer UI externa**.

## 4. Funcionalidades

**Comandos do usuário** (`/alethe ...`): `setup`, `<tarefa>`, `always`, `freestyle`, `status`, `stop`, `report`, `off`, `editor`, `mark`.

**Playbooks:** `direct`, `cascade`, `scout-then-build`, `plan-big-execute-small`, `audit-at-scale`, `parallel-worktrees`,
`plan-execute`, `advisor-escalation`, `best-of-n`, `review-gate`, `complete`.

**Mudança de rota no meio do caminho:** `skip`, `add`, `switch`, `freeform` entre waves, com justificativa no relatório.

**Join mid-task:** entra no meio do workflow de outra skill, usando spec e diff como plano.

**Providers:** Claude Code, Codex, Antigravity (`agy`), Kimi, e CLIs customizadas (adapters). Resume suportado nos quatro primeiros.

**Custo real:** tokens e custo conforme reportados por cada CLI, separados em usado e descartado; nunca estimados.

**Runner CLI** (`python scripts/orchestrate.py ...`): `probe, doctor, strategies, policy, playbook, usage, models, scaffold,
validate, dry-run, new, run, wait, budget, note, status, stop, mark, report, list, use, editor, mode, hook`.

**Qualidade:** testes em `tests/` com CLIs falsas, CI em `.github/workflows/ci.yml`, validação de playbooks em `tools/`.

## 5. Alethe Bar para Windows

O plano resultou em um widget dentro de `widget/`, implementado com Tauri 2, Rust e HTML, em vez do MVP em tkinter inicialmente proposto. A versão do aplicativo é **0.1.0**, independente da versão 21 da skill. A configuração Tauri prevê um instalador NSIS que inclui a skill.

```text
widget/
├── ui/index.html             interface e personagens desenhados em canvas
├── src-tauri/src/main.rs     comandos, leitura do estado e integração com Windows
├── src-tauri/tauri.conf.json janela, recursos e instalador
└── package.json              comandos de desenvolvimento e build
```

A barra consulta `current.json` e os arquivos `state.json` a cada segundo, mostra os providers com workers em execução e anima estados de repouso, trabalho, conclusão ou erro. O estado só identifica workers: a atividade do agente líder não é monitorada. A janela fica no topo e pode ser arrastada; salvar sua posição não está implementado.

O painel oferece configuração da skill, perfil e objetivo, seleção de projeto, abertura de CLI, diagrama, inicialização com Windows e verificação de atualização. O monitoramento lê o estado gravado pelo runner, mas as ações de setup e inicialização alteram configurações do usuário. O setup do widget requer Python; o uso manual da skill sem Python continua possível fora dele.

Na árvore de trabalho atual, o campo de pedido abre Claude ou Codex com o texto adaptado: caracteres fora da lista permitida são substituídos por espaços e o texto é limitado a 1.500 caracteres. Antigravity e Kimi abrem sem receber o pedido. Essas alterações locais ainda não foram validadas em uma sessão interativa.

### Verificação do plano

1. **Implementado no código:** barra Tauri, leitura de estado, múltiplos providers ativos, animações, seleção de projeto, setup, configuração de início com Windows e receita para instalador NSIS.
2. **Ainda pendente:** exibir atividade do líder e salvar a posição da janela. Os personagens são desenhados em canvas, sem sprite sheets separados.
3. **Verificação necessária:** testar setup, abertura das CLIs, diagrama, animações e instalador no Windows. Compilar o código não confirma esses fluxos.
