# 📡 Open WiFi Survey

> Ferramenta desktop para planejamento e simulação de redes Wi-Fi sobre plantas baixas, com heatmap de sinal, simulação de paredes e obstáculos, posicionamento inteligente de APs e zoom interativo.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)
![Tkinter](https://img.shields.io/badge/UI-Tkinter-informational?style=flat-square)
![Pillow](https://img.shields.io/badge/Imaging-Pillow-green?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

---

## ✨ Funcionalidades

### 🗺️ Carregamento de Planta
Importe qualquer planta baixa nos formatos PNG, JPG, BMP ou TIFF. A imagem serve como base para todo o planejamento e é embutida no arquivo de projeto ao salvar.

### 📡 Posicionamento de Access Points
Adicione APs manualmente clicando sobre a planta ou utilize o posicionamento automático. O sistema calcula a quantidade recomendada de APs com base na área informada (1 AP a cada 100 m²) e os distribui em grade otimizada automaticamente. Cada AP pode ser movido por arrastar e removido com clique direito.

### 🌡️ Heatmap de Cobertura (Modelo Logarítmico)
A cobertura de sinal é calculada com o modelo **Log-Distance Path Loss** (expoente 2.8, representando ambientes indoor), muito mais realista que a simples distância linear. O heatmap é renderizado em três faixas de qualidade de sinal:

| Cor | Qualidade | Limiar |
|-----|-----------|--------|
| 🟢 Verde | Ótimo | > 66% |
| 🟡 Amarelo | Regular | 33–66% |
| 🔴 Vermelho | Fraco | 10–33% |
| ⬛ Cinza | Sem cobertura | < 10% |

### 🧱 Simulação de Paredes e Obstáculos
Ative o **Modo Parede** e desenhe segmentos de parede diretamente sobre a planta com click e arraste. Cada parede tem um material com atenuação de sinal fisicamente calibrada:

| Material | Perda de Sinal |
|----------|---------------|
| Vidro | −2 dB |
| Drywall | −3 dB |
| Tijolo | −8 dB |
| Concreto | −15 dB |
| Metal | −20 dB |

O motor de heatmap faz **interseção de raios** entre cada AP e cada célula do mapa, acumulando a atenuação de todas as paredes cruzadas pelo sinal. Paredes são visualmente representadas em cores distintas por material e são salvas no projeto.

### 🔍 Zoom Interativo
Navegue pela planta com zoom de 15% a 500% usando os botões da toolbar ou **Ctrl + scroll do mouse**. Todos os cliques, detecção de APs e paredes se adaptam automaticamente ao nível de zoom. O botão **1:1** restaura o zoom original instantaneamente.

### 📶 Suporte Dual-Band
Alterne entre **2.4 GHz** (raio de cobertura de 12 m) e **5 GHz** (raio de 8 m) com um clique. O heatmap é recalculado imediatamente. Cada AP pode ser configurado individualmente com a banda desejada.

### 🔁 Undo / Redo Completo
Histórico de até 60 ações abrangendo adição, remoção e movimentação de APs e paredes. Atalhos **Ctrl+Z** (desfazer) e **Ctrl+Y** (refazer) disponíveis em todo momento.

### 💾 Salvar e Carregar Projetos
Projetos são salvos em formato **JSON**, incluindo a imagem da planta em Base64, todos os APs com posição, banda e label, todas as paredes com coordenadas e material, e as configurações de área e escala. Totalmente portátil — um único arquivo contém tudo.

### 🖼️ Exportar Imagem
Exporte o mapa de cobertura completo como PNG, com heatmap, paredes coloridas por material, marcadores de AP e uma **legenda embarcada** indicando as faixas de sinal e os materiais de parede.

### 🔢 Contador e Recomendação de APs
A barra de status exibe em tempo real quantos APs estão posicionados, quantas paredes foram desenhadas e compara com o número recomendado pelo cálculo de área — com indicadores visuais verde (ideal), amarelo (excesso) e vermelho (insuficiente).

### 💬 Tooltips Contextuais
Ao passar o mouse sobre um AP, um tooltip exibe o nome, a banda e a posição em metros (calculada pela escala real informada). Ao passar sobre uma parede, exibe o material e a atenuação em dB.

---

## 🏗️ Arquitetura

O projeto segue separação clara entre camadas:

```
AccessPoint   →  entidade de domínio para cada ponto de acesso
Wall          →  entidade de domínio para segmentos de parede
HeatmapEngine →  motor de cálculo de propagação de sinal
ProjectModel  →  estado do projeto (imagem, APs, paredes, escala)
WifiPlannerApp→  interface gráfica e coordenação de eventos
```

---

## 🚀 Instalação

**Arch Linux / CachyOS / Manjaro:**
```bash
sudo pacman -S tk
pip install pillow --break-system-packages
python main.py
```

**Ubuntu / Debian:**
```bash
sudo apt install python3-tk
pip install pillow
python3 main.py
```

**Windows / macOS:**
```bash
pip install pillow
python main.py
```

> O `tkinter` já vem incluído nos instaladores oficiais do Python para Windows e macOS.

---

## 🎮 Como usar

1. Clique em **📂 Carregar Planta** e selecione a imagem da planta baixa
2. Informe a **Área (m²)** e a **Largura real (m)** para calibrar a escala
3. Clique em **⚡ Auto** para posicionamento automático ou clique na planta para adicionar APs manualmente
4. Ative **🧱 Modo Parede**, selecione o material e arraste para desenhar obstáculos
5. Use **Ctrl+scroll** para navegar pela planta com zoom
6. Salve o projeto com **💾 Salvar Projeto** ou exporte a imagem com **🖼 Exportar Imagem**

---

## 🛠️ Tecnologias

- **Python 3.10+**
- **Tkinter** — interface gráfica nativa
- **Pillow (PIL)** — renderização do heatmap e manipulação de imagem
- **JSON + Base64** — formato de projeto portátil

---

## 📄 Licença

MIT License — sinta-se livre para usar, modificar e distribuir.
