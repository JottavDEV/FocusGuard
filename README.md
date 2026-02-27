# FocusGuard

FocusGuard é um aplicativo desktop para Windows que bloqueia aplicativos em tempo real, com organização por blocos, regras de horário e modo de desbloqueio com desafios.

## Funcionalidades principais

- Bloqueio de processos por nome (`.exe`) em tempo real
- Seleção de aplicativo por navegador de arquivos
- Organização por blocos (ex.: Jogos, Trabalho, Redes Sociais)
- Regras de horário por bloco (bloquear/liberar por período)
- Indicador de status e controle pela bandeja do sistema
- Inicialização com Windows (opcional)
- Persistência local das configurações em `%APPDATA%\FocusGuard\config.json`
- Dificuldade de desbloqueio opcional (puzzles + senha)

## Requisitos

- Windows 10/11
- Python 3.10+ (recomendado 3.12+)

## Instalação (desenvolvimento)

```bash
python -m pip install -r requirements.txt
```

## Execução

```bash
python main.py
```

## Gerar executável (.exe)

```bash
python -m PyInstaller --noconfirm --clean --onefile --windowed --name FocusGuard --hidden-import customtkinter --collect-all customtkinter --collect-all pystray --collect-all PIL main.py
```

Saída:

- `dist/FocusGuard.exe`

## Gerar instalador (Setup)

Pré-requisito:

- Inno Setup 6 instalado (`ISCC.exe`)

Build completo (app + instalador):

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build-installer.ps1
```

Com versão específica:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build-installer.ps1 -Version 1.0.0
```

Saídas:

- `release/FocusGuard-Setup-vX.Y.Z.exe`
- `release/SHA256-vX.Y.Z.txt`

## Estrutura do projeto

- `main.py` - Aplicação principal
- `requirements.txt` - Dependências Python
- `VERSION` - Versão da release
- `installer/FocusGuard.iss` - Script do instalador Inno Setup
- `installer/build-installer.ps1` - Pipeline local de build/release

