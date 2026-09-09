# get_room_views — olhar a planta antes de decidir

Data: 2026-09-09. Aprovado pelo Gabriel na conversa de 09/09.

## Problema
O agente posiciona luminárias, interruptores e comandos de iluminação raciocinando por
coordenadas e bounding box, sem nunca ver a planta. Resultado: correções repetidas
ambiente a ambiente (Vila Jabuticaba, 6725). Custo de contexto alto por causa dos JSONs
de elementos de pavimento inteiro.

## Solução
Uma ferramenta MCP, `get_room_views(room, width_px, margin_mm)`, que devolve para UM
ambiente: planta baixa e planta de forro recortadas em PNG, com marcadores numerados,
mais metadados curtos (portas com lado de abertura, luminárias, teclas, mobiliário,
pé-direito medido pelo forro real).

## Componentes
1. `revit_mcp/room_views.py` (IronPython, dentro do Revit) — rota `POST /room_views/`.
   - Localiza o ambiente por ID ou por nome (comparação só por [a-z0-9], imune a acento).
   - TransactionGroup: T1 cria FloorPlan + CeilingPlan temporárias do nível do ambiente,
     recorta pelo bbox do ambiente + margem, detalhe Fino, escala 1:50, sem template,
     anotações/grades/refs escondidas, faixa de vista: planta corte 1,60 m; forro topo =
     forro real + 0,8 m. Commit. Exporta PNG (largura fixa = width_px, FitDirection
     Horizontal) para pasta ASCII em %TEMP%. T2 apaga as vistas. Assimilate.
   - Metadados: ambiente (nome, número, nível, área, pés-direitos medidos pelos Ceilings
     que cobrem o ambiente), crop em metros (coordenadas de projeto), portas (posição,
     largura, dobradiça, abre para dentro/fora), luminárias (pai quando aninhada, tipo,
     z, letra em Comentários), teclas (Módulos, agrupadas por ponto, Switch ID),
     mobiliário/louças/equipamentos (nome, centro). Pertencimento por
     `Room.IsPointInRoom` no ponto (x, y, nível + 0,5 m) para ignorar limite superior
     errado do ambiente.
2. `tools/view_tools.py` (CPython, lado MCP) — ferramenta `get_room_views`.
   - POST com timeout 120 s. Decodifica PNGs; converte coordenadas de projeto em pixel
     usando o tamanho real da imagem e o crop. Carimba com Pillow: L1..Ln nas luminárias
     (forro), S1..Sn nas teclas e D1..Dn nas portas (planta). Devolve
     [resumo JSON compacto, Image planta, Image forro].
   - Corrige `get_revit_view`, que convertia a Image em texto.
3. Memória: regra "antes de posicionar/definir comando, chamar get_room_views e
   raciocinar pela imagem; depois de gravar, chamar de novo para conferir".

## Fora de escopo
Tomadas, elevações, escrita no modelo, controle de tela.

## Teste
Manual, com o Revit aberto na Vila Jabuticaba: um ambiente com luminárias e teclas
conhecidas; conferir que os marcadores caem sobre os símbolos e que as vistas
temporárias não sobram no navegador.
