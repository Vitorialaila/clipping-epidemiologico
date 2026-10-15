# Clipping Epidemiológico

Buscador automático de notícias de relevância epidemiológica em fontes oficiais, com feed atualizado diariamente e clipping semanal em PDF, organizado por semana epidemiológica (SE).

## O que o sistema faz

1. **Coleta diária (6h, horário de Brasília).** Lê as publicações de OMS, OPAS, ECDC, CDC, Africa CDC, Ministério da Saúde, Agência Brasil, Agência Fiocruz, SES-GO e Agência Brasília.
2. **Triagem por regras.** Cada notícia recebe uma pontuação de relevância com base no agravo (pesos alinhados à lista de notificação compulsória), na natureza do evento (surto, alerta, ESPII, primeiro caso, óbitos), na proximidade geográfica (DF > Brasil > Américas > internacional) e no peso da fonte.
3. **Clipping semanal (domingo).** Ao fim de cada SE, as notícias mais pontuadas passam por uma triagem com IA (Google Gemini, plano gratuito), que elimina repetições do mesmo evento e escolhe os destaques. Cada destaque recebe título no padrão quem, quando e onde e um resumo técnico em português, redigido a partir do texto original. O resultado é um PDF com cabeçalho institucional.
4. **Imagens.** A capa traz o mapa da semana, com os destaques numerados por localização e um quadro do Brasil por UF. Cada destaque recebe uma imagem, nesta ordem de preferência: foto da própria notícia, somente quando a fonte tem licença de reutilização verificada (hoje, portais gov.br do Ministério da Saúde e da Anvisa, CC BY-ND 3.0, exibida inteira e com crédito); mapa de localização do evento; ou pictograma do grupo de agravo. Mapas e pictogramas são desenhados pelo próprio sistema.
5. **Site (GitHub Pages).** Exibe a edição mais recente com o mapa da semana, o feed completo com filtros (agravo, fonte, escopo e busca livre) e o arquivo de todas as edições em PDF.

Nenhuma edição é sobrescrita: se uma SE for gerada novamente, o sistema cria uma nova versão (`-v2`, `-v3`) e mantém as anteriores.

## Configuração inicial (uma única vez)

1. **Chave gratuita do Gemini.** Acesse <https://aistudio.google.com/apikey> com uma conta Google, clique em *Create API key* e copie a chave. No repositório, vá em *Settings > Secrets and variables > Actions > New repository secret*, use o nome `GEMINI_API_KEY` e cole a chave.
2. **Permissão de escrita das Actions.** Em *Settings > Actions > General > Workflow permissions*, marque *Read and write permissions* e salve.
3. **GitHub Pages.** Em *Settings > Pages*, escolha *Deploy from a branch*, ramo `main` e pasta `/docs`. O endereço do site aparece nessa mesma tela após a primeira publicação.
4. **Primeira execução.** Em *Actions > Clipping epidemiológico > Run workflow*, mantenha a etapa `tudo` e clique em *Run workflow*. A primeira execução coleta as notícias disponíveis nos feeds e já gera a edição da última SE encerrada.

O plano gratuito do Gemini tem limites de requisições por minuto e por dia. O sistema faz cerca de 12 chamadas por semana, espaçadas, e tenta modelos alternativos se um deles estiver sem cota. Se a IA ficar indisponível (sem chave, cota esgotada ou fim do plano gratuito), o clipping continua sendo gerado: os destaques passam a ser escolhidos apenas pelas regras e os resumos viram trechos iniciais do texto original. Para usar a API paga do Claude no lugar do Gemini, troque `provedor` para `anthropic` no `config/config.yaml` e cadastre o segredo `ANTHROPIC_API_KEY`.

## Uso no dia a dia

O processo é automático. Para ações manuais, use *Actions > Clipping epidemiológico > Run workflow*:

| Campo | Uso |
|---|---|
| `etapa` | `tudo` (padrão), `coletar`, `clipping`, `site` ou `repontuar` |
| `se` | Gera a edição de uma SE específica, no formato AAAASS (ex.: `202640`) |
| `forcar` | Gera uma nova versão de uma edição que já existe, sem apagar a anterior |

`repontuar` recalcula a pontuação de toda a base depois de uma alteração em `config/criterios.yaml`.

## Ajustes

Todos os ajustes são feitos editando arquivos diretamente no GitHub:

- **`config/config.yaml`**: título, subtítulo e rodapé da publicação; modelos de IA; número de destaques por edição (padrão 10); limite de destaques sobre o mesmo agravo; fontes monitoradas.
- **`config/criterios.yaml`**: agravos e seus termos de busca (português, inglês e espanhol), pesos, termos de evento, localidades e termos que reduzem a pontuação.
- **`assets/img/logos.png`**: imagem do cabeçalho. Para trocar, substitua o arquivo mantendo o mesmo nome (de preferência em alta resolução, com fundo branco ou transparente).
- **`templates/clipping.html`** e **`templates/site.html`**: layout do PDF e do site.

### Adicionar uma fonte

Inclua um bloco em `fontes` no `config/config.yaml`. Para feeds RSS basta a URL do feed; para páginas sem RSS use `tipo: html` e informe em `padrao_link` uma expressão regular que identifique os links das notícias na página de listagem.

## Estrutura

```
config/            configuração geral, fontes e critérios de relevância
src/clipping/      código (coleta, pontuação, IA, PDF, site)
templates/         layout do PDF e do site
assets/            logos, fontes tipográficas (licença OFL) e bases cartográficas
data/noticias.json base de notícias coletadas
data/edicoes/      dados de cada edição (destaques e resumos)
docs/              site publicado no GitHub Pages, com os PDFs em docs/edicoes/
```

## Executar localmente (opcional)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # opcional
PYTHONPATH=src python -m clipping tudo
```

O PDF exige as bibliotecas do sistema usadas pelo WeasyPrint (Pango). Em Linux: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`.

## Observações

- Bases cartográficas: Natural Earth 1:50m (domínio público) e malha das unidades federativas derivada do IBGE (repositório click_that_hood), simplificadas em `assets/geo/`.
- Para liberar fotos de outra fonte, confirme a licença de reutilização no portal e acrescente o domínio em `imagens > fotos_licenciadas` no `config/config.yaml`.
- A semana epidemiológica segue a regra adotada pelo Ministério da Saúde: começa no domingo e termina no sábado.
- Alguns portais podem bloquear acessos automatizados de forma intermitente. Quando isso ocorre, a falha é registrada no log da execução e a coleta segue com as demais fontes. A Agência Brasília está marcada como fonte experimental por esse motivo.
- Os resumos são gerados a partir do texto das fontes oficiais, com instruções para não acrescentar dados. Para uso técnico, consulte sempre a publicação de origem, indicada em cada item.
- O GitHub Pages gratuito exige repositório público. O GitHub pode pausar agendamentos de repositórios sem atividade por 60 dias; os commits diários da coleta normalmente mantêm o repositório ativo, mas, se o agendamento for pausado, basta reativá-lo na aba *Actions*.
