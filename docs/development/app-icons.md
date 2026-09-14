# Anwendungssymbole

Die vom Benutzer bereitgestellte Vorlage liegt lokal in `data/logo_preview.png`.
Die Anwendung lädt ihre Symbole ausschließlich aus den mitgelieferten Ressourcen
unter `packages/client/src/papagui_client/resources/icons/`.

| Dateien | Verwendung |
| --- | --- |
| `papagui-client.png/.ico/.icns` | Client, rot, Lupe und Dokument |
| `papagui-server.png/.ico/.icns` | Indexserver-Verwaltung, blau, drei Servereinschübe |

Beide Motive haben einen transparenten Hintergrund und keine Wortmarke.
PNG-Dateien enthalten die hochauflösenden Vorlagen. ICO-Dateien enthalten
16, 20, 24, 32, 40, 48, 64, 96, 128 und 256 Pixel; ICNS-Dateien enthalten
auch die großen macOS-Auflösungen bis 1024 Pixel.

`gui/icons.py` lädt die Ressourcen relativ zum installierten Python-Paket,
auch im entpackten PyInstaller-Bundle. Die Anwendung setzt ihr Standardsymbol
vor dem Erstellen der Fenster, damit eigenständige Dialoge und Vorschaufenster
es erben. Hauptfenster und Indexserver-Fenster setzen zusätzlich ihr jeweiliges
Symbol explizit; ihre Tray-Icons übernehmen dasselbe Motiv. Unter Windows wird
vor `QApplication` die Kennung `de.papagui.client` beziehungsweise
`de.papagui.tray` gesetzt, auch beim Start über `pythonw.exe`.

Das Serversymbol gehört zum Indextray. Der Docker-Server selbst hat keine
Desktopoberfläche. Batch- und Shellskripte besitzen kein einbettbares
Anwendungssymbol; Windows-Verknüpfungen können auf die jeweilige ICO-Datei
verweisen. Die PyInstaller-EXE-Dateien erhalten die Symbole direkt beim Build.

## Erzeugung und Export

Die PNG-Motive wurden mit dem eingebauten Imagegen-Werkzeug aus dem Logo
abgeleitet. Für den Server wurde der erste Entwurf mit den waagerechten
Einschüben verwendet. Nach ausdrücklicher Zustimmung des Benutzers wurde dessen
fälschlich eingezeichnetes Schachbrett mit Python/Pillow entfernt: nur neutrale
Hintergrundpixel, die mit dem Bildrand verbunden sind, wurden transparent;
weiße und graue Details innerhalb des Emblems blieben erhalten.

Auf Benutzerwunsch wurde das Servermotiv anschließend mit dem eingebauten
Imagegen-Werkzeug von Rot zu Blau umgefärbt. Der Hintergrund wurde mit der
bereits autorisierten Python-Freistellung wieder transparent gemacht; alle
nativen Icon-Dateien wurden aus der blauen PNG-Vorlage neu exportiert.

Verwendeter Umfärbungs-Prompt (Eingabe: rotes Servermotiv):

```text
Edit target: the provided PapaGUI SERVER logo PNG. Change only the RED color palette to BLUE: vivid royal blue for bright red areas, deep navy blue for dark red shadows and outlines, preserving the relative shading and brightness. This includes the circular backdrop, fox details, server drawer outlines and indicator dots. Preserve the white fox, petals, all white highlights, three server drawers, exact shapes, dimensions, composition, smooth edges and existing transparent background. No new elements, no lettering, no redraw, no checkerboard or solid rectangular background. Output a single blue-and-white server icon PNG with actual alpha transparency.
```

Native Dateiformate lassen sich mit einem Entwicklungsinterpreter mit Pillow
erneut exportieren:

```powershell
.venv/Scripts/python.exe tools/build_app_icons.py
```

Der Export verändert die PNG-Vorlagen nicht. Pillow ist nur zum erneuten
Export erforderlich, nicht beim Start oder beim Erstellen eines Builds aus
den bereits vorhandenen Icon-Dateien.

Verwendeter Client-Prompt (Eingabe: ursprüngliches Logo):

```text
Use case: precise-object-edit. Asset type: PapaGUI desktop CLIENT application icon, square PNG with real transparent background outside the logo. The provided image is the edit target, an existing logo, not a loose style reference. Remove ONLY the PapaGUI wordmark below the emblem, and center the remaining red-and-white fox, red circular background, petals and document/magnifying glass emblem on a square canvas with a narrow even transparent margin (about 4%). Preserve the original fox identity, left-facing pose, facial shape, ears, red circle, red and white palette, document and magnifying glass, and smooth curves as faithfully as possible. Clean stray edge pixels from the preview. No lettering, no watermark, no checkerboard rendered into the image, no added badges, no new shapes, no surrounding mockup. Output a single ready-to-use icon artwork with genuine alpha transparency, at 1024 by 1024 or higher.
```

Verwendeter Server-Prompt (Eingabe: Client-Motiv ohne Schrift):

```text
Use case: precise-object-edit. Asset type: PapaGUI INDEXSERVER application icon, paired with the provided client icon. The supplied PNG is the edit target. Keep the entire red circular emblem, left-facing white fox, ears, face, fur, petals, color palette, transparent outside background and composition identical. Change only the small document-and-magnifying-glass symbol in the lower-left foreground: replace it with a clear compact server tower made of three stacked rounded horizontal white drive trays, thick dark red outlines, and one red indicator dot on each tray. The server symbol should occupy the same foreground location and similar area as the document and lens it replaces, and should remain recognizable at small icon sizes. Remove the magnifying glass and document entirely. Preserve the surrounding fox and curling tail. No words or letters anywhere. No extra scene or mockup. Square PNG, genuine alpha transparency outside emblem, narrow even margin, 1024 by 1024 or higher. Deliver exactly one server icon.
```
