# Simple Sticky Notes for Codex

[![Tests](https://github.com/JFETA/simple-sticky-notes-codex/actions/workflows/tests.yml/badge.svg)](https://github.com/JFETA/simple-sticky-notes-codex/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Integración local y no oficial que permite a Codex buscar, crear y organizar notas en **Simple Sticky Notes 6.9.0.0** para Windows mediante MCP.

> This is an unofficial community project. It is not affiliated with or endorsed by Simnet Limited.

## Funciones

- Consultar libretas y buscar notas activas.
- Leer una nota por ID.
- Crear notas y libretas.
- Mover notas entre libretas.
- Marcar o desmarcar favoritas.
- Evitar duplicados al reintentar una creación con el mismo UUID.
- Mostrar Markdown habitual de Codex como texto enriquecido: negrita, cursiva, código breve, enlaces, viñetas, tareas y listas numeradas; conservar emojis.
- Reformatear una nota existente con una comprobación exacta del texto anterior.

El conector no expone SQL libre ni elimina notas. `format_note` solo modifica la presentación y el texto visible de una nota cuando el contenido actual coincide exactamente con `expected_text`.

## Seguridad de las escrituras

Antes de modificar `Notes.db`, el servidor:

1. comprueba la versión de la aplicación y el esquema SQLite;
2. solicita un cierre normal de Simple Sticky Notes y aborta si no termina;
3. crea un respaldo con marca temporal;
4. aplica una sola transacción y ejecuta `PRAGMA quick_check`;
5. reabre la aplicación si estaba abierta.

Los respaldos se guardan en `%LOCALAPPDATA%\Codex\simple-sticky-notes\backups`.

## Requisitos

- Windows 10 u 11.
- Simple Sticky Notes 6.9.0.0.
- Python 3.11 o posterior.
- Codex CLI o Codex desktop con soporte MCP local.

## Instalación

```powershell
git clone https://github.com/JFETA/simple-sticky-notes-codex.git
cd simple-sticky-notes-codex
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

El instalador crea `.venv`, instala las dependencias, registra el servidor MCP y copia la skill a `~/.codex/skills`. Después, inicia una tarea nueva en Codex.

Para una ubicación personalizada de la base o del ejecutable, define `SSN_DB_PATH` y `SSN_EXE_PATH` en el entorno del servidor MCP.

## Uso

- `Guarda este resumen en la libreta Trabajo y márcalo como favorito.`
- `Busca mis notas que mencionen presupuesto.`
- `Mueve las notas 12 y 14 a la libreta Ideas.`
- `Da formato a la nota 19 para que se vea como en Codex.`

`create_note` interpreta Markdown de forma predeterminada. Para mostrar los caracteres Markdown literalmente, indica `format="plain"`. Las listas numeradas conservan sus números; las viñetas y casillas se muestran con símbolos Unicode. Los enlaces muestran su etiqueta y URL visible para conservar la dirección. No se promete reproducir tablas ni bloques complejos de la interfaz de Codex.

Las herramientas de escritura requieren aprobación en la configuración incluida del plugin.

## Desarrollo

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m unittest discover -s tests -v
```

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para proponer cambios y [SECURITY.md](SECURITY.md) para reportar vulnerabilidades.

## Compatibilidad y riesgos

El formato SQLite pertenece a una aplicación de terceros y no constituye una API pública. Esta versión rechaza otros esquemas y versiones. Conserva respaldos y prueba las actualizaciones de Simple Sticky Notes antes de habilitar escrituras.

## Licencia

Código distribuido bajo la [licencia MIT](LICENSE). Simple Sticky Notes y sus marcas pertenecen a sus respectivos propietarios.
