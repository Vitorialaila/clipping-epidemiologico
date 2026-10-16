# Rotina semanal: Boletim Epidemiológico a partir do SITREP

Executada toda terça-feira às 14h (Brasília). O SITREP da semana anterior fica pronto na terça de manhã.
Modelo completo de referência: `data/boletins/2026-40/boletim.json` e o PDF `docs/boletins/boletim-SE40-2026.pdf`.

## Regras de conteúdo (obrigatórias)

- Nunca usar travessão (—). Usar vírgula, dois-pontos ou parênteses.
- Títulos no padrão "quem, quando e onde" (ex.: "Goiás registra 4.348 casos prováveis de dengue nas SE 36 a 39 de 2026").
- Só fontes oficiais, as mesmas citadas no slide.
- O texto corrido de cada análise sai dos **comentários do Canva** daquele slide, reescrito em prosa técnica, com erros de digitação corrigidos.
  As anotações do apresentador (presenter notes) estão desatualizadas: **ignorar sempre**.
- Quando o número do comentário divergir do slide, usar o valor do slide e anotar a divergência para o relatório final.
- Não incluir o slide de autores/equipe nem o nome de nenhuma pessoa como autoria no PDF (a autoria aparece só no rodapé do site).
- Nunca sobrescrever nem apagar arquivos entregues: o gerador cria `-v2`, `-v3` com `--forcar`.
- Nunca alterar cores nem identidade das logos.
- Nada de colar o slide inteiro: entram só as figuras (mapas, gráficos, fotos). Números viram `indicadores`,
  tabelas do slide viram `tabela`, cronologias viram `linha_tempo`.

## Passo a passo

1. **Semana.** A SE do boletim é a semana epidemiológica encerrada no sábado anterior (padrão MS, domingo a sábado).
   Pasta da edição: `data/boletins/AAAA-SS/` (ex.: `2026-41`). Se `docs/boletins/boletim-SESS-AAAA.pdf` já existir, parar e avisar.
2. **Repositório.** Clonar `Vitorialaila/clipping-epidemiologico` (ou `git pull` se já houver cópia).
3. **Canva.** `search-designs` com "SITREP SE SS/26" (o número da SE com dois dígitos e o ano com dois).
   Se não existir ainda, parar e avisar a usuária (o SITREP pode ter atrasado).
4. **Ler o SITREP.** `read-design` página a página (campo `design_content`) para ter o texto de cada slide.
   Slides com o texto "Slide oculto" ficam de fora. Ignorar `presenter_notes`.
5. **Comentários.** `list-comments` (com respostas via `list-replies` quando houver). A API não informa o slide do comentário:
   associar cada comentário ao slide pelo conteúdo (números, lugares, agravo). Excluir:
   comentários de revisão ("Qual ano?", "Erro de digitação", "rever essa decisão" etc.), resolvidos e duplicados (manter o mais recente).
6. **Exportar slides.** `export-design` em PNG, largura 2400. Gravar `data/boletins/AAAA-SS/exportacao.json` no formato
   `{"design_id": ..., "titulo_canva": ..., "largura": 2400, "urls": [...]}` e fazer commit e push **imediatamente**
   (os links expiram em poucas horas; o ambiente local não consegue baixá-los). Disparar o download:
   `gh api -X POST repos/Vitorialaila/clipping-epidemiologico/actions/workflows/clipping.yml/dispatches -f ref=main -f 'inputs[etapa]=boletim-baixar' -f 'inputs[edicao]=AAAA-SS'`
   Acompanhar com `gh api repos/Vitorialaila/clipping-epidemiologico/actions/runs?per_page=1` até `status=completed`, e então `git pull`.
   Os slides ficam em `data/boletins/AAAA-SS/slides/NN.png`.
7. **Recortes.** Abrir cada slide (gerar uma grade de coordenadas para ajudar) e definir em `paineis` os retângulos das figuras
   na base 960 x 540: `{"r": [x0, y0, x1, y1], "p": "título do painel", "apagar": [[x0, y0, x1, y1]]}`.
   `apagar` pinta de branco a linha "Fonte:" do slide ou caixas que invadam o recorte. Sem caixas de texto, cards ou títulos do slide.
   Conferir cada recorte em `docs/boletins/img/AAAA-SS/` depois de gerar.
8. **boletim.json.** Mesmo esquema da SE 40: `edicao`, `se` ("AAAASS"), `origem`, `resumo` (um parágrafo da semana),
   `secoes` com `id`, `nome`, `risco` ("Risco 0" a "Risco 4", conforme o slide de abertura do eixo), `risco_desc` e `itens`
   (`slide`, `titulo`, `figura` = legenda, `texto`, `paineis`, `indicadores` [{v, l, d}], `tabela` opcional, `linha_tempo` opcional, `fonte`).
9. **Gerar e conferir.** `pip install -r requirements.txt` se preciso, depois
   `PYTHONPATH=src python3 -m clipping boletim --edicao AAAA-SS`. Converter algumas páginas do PDF em imagem e conferir:
   recortes limpos, sem títulos órfãos no pé da página, sem travessões.
10. **Publicar.** `git add data/boletins/AAAA-SS docs` e commit; `git fetch origin main && git rebase origin/main && git push`.
11. **Avisar a usuária** com: link do site (https://vitorialaila.github.io/clipping-epidemiologico/#boletim), o PDF anexado,
    a lista de divergências entre comentários e slides, slides deixados de fora e qualquer comentário que não deu para associar.
