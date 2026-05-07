import os
from pathlib import Path
from datetime import datetime, timedelta
import random
import string
from PIL import Image
import openpyxl
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

def generate_mock_data(base_path: Path, num_customers: int = 5, files_per_customer: int = 50):
    base_path.mkdir(parents=True, exist_ok=True)
    
    customers = [
        "Müller GmbH",
        "Schmidt Elektro",
        "Weber Consulting",
        "Bauer Handels KG",
        "Fischer Immobilien"
    ][:num_customers]
    
    service_types = [
        "Energieberatung",
        "Bauvorbereitung",
        "Projektmanagement",
        "Audits"
    ]
    
    years = ["2022", "2023", "2024", "2025"]
    
    file_types = [
        ("pdf", _generate_pdf),
        ("xlsx", _generate_excel),
        ("txt", _generate_text),
        ("jpg", _generate_image),
        ("docx", _generate_docx),
        ("csv", _generate_csv),
    ]
    
    generated_count = 0
    
    for year in years:
        for service_type in service_types:
            for customer in customers:
                customer_path = base_path / year / service_type / customer
                customer_path.mkdir(parents=True, exist_ok=True)
                
                subfolders = ["Hausbegehung", "Berichte", "Verträge", "Rechnungen", "Dokumentation"]
                for subfolder in random.sample(subfolders, k=random.randint(2, 4)):
                    subfolder_path = customer_path / subfolder
                    subfolder_path.mkdir(exist_ok=True)
                    
                    num_files = random.randint(3, 8)
                    for i in range(num_files):
                        file_type, generator = random.choice(file_types)
                        filename = f"{subfolder.lower()}_{i:02d}.{file_type}"
                        filepath = subfolder_path / filename
                        
                        try:
                            generator(filepath)
                            generated_count += 1
                        except Exception as e:
                            print(f"Fehler beim Generieren von {filepath}: {e}")
                
                num_direct_files = random.randint(3, 5)
                for i in range(num_direct_files):
                    file_type, generator = random.choice(file_types)
                    filename = f"document_{i:02d}.{file_type}"
                    filepath = customer_path / filename
                    
                    try:
                        generator(filepath)
                        generated_count += 1
                    except Exception as e:
                        print(f"Fehler beim Generieren von {filepath}: {e}")
    
    print(f"✓ {generated_count} Mock-Dateien generiert in {base_path}")
    return generated_count


def _generate_pdf(filepath: Path):
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        
        c = canvas.Canvas(str(filepath), pagesize=letter)
        c.drawString(100, 750, f"Dokument: {filepath.stem}")
        c.drawString(100, 730, f"Erstellt: {datetime.now().strftime('%d.%m.%Y')}")
        
        text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10
        y = 700
        for line in text.split():
            c.drawString(100, y, line)
            y -= 20
        
        c.save()
    except ImportError:
        filepath.write_text("Mock PDF Content")


def _generate_excel(filepath: Path):
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Daten"
        
        headers = ["Datum", "Kunde", "Betrag", "Status", "Bemerkungen"]
        ws.append(headers)
        
        for i in range(10):
            row = [
                (datetime.now() - timedelta(days=i)).strftime("%d.%m.%Y"),
                f"Kunde_{i}",
                random.randint(100, 10000),
                random.choice(["Offen", "Bearbeitet", "Abgeschlossen"]),
                f"Bemerkung {i}"
            ]
            ws.append(row)
        
        wb.save(filepath)
    except Exception as e:
        filepath.write_text("Mock Excel Content")


def _generate_text(filepath: Path):
    content = "\n".join([
        f"Dokumentation für {filepath.parent.name}",
        f"Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        "",
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
        "" + "\n".join([f"- Punkt {i}" for i in range(5)])
    ])
    filepath.write_text(content, encoding='utf-8')


def _generate_image(filepath: Path):
    try:
        img = Image.new('RGB', (400, 300), color=(random.randint(50, 255), 
                                                    random.randint(50, 255), 
                                                    random.randint(50, 255)))
        img.save(filepath)
    except Exception:
        filepath.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)


def _generate_docx(filepath: Path):
    try:
        from docx import Document
        doc = Document()
        doc.add_heading(f'Dokument: {filepath.stem}', 0)
        doc.add_paragraph(f'Erstellt: {datetime.now().strftime("%d.%m.%Y")}')
        doc.add_paragraph('Lorem ipsum dolor sit amet, consectetur adipiscing elit.')
        doc.save(filepath)
    except ImportError:
        filepath.write_text("Mock DOCX Content")


def _generate_csv(filepath: Path):
    lines = [
        "Datum,Kunde,Betrag,Status",
    ]
    for i in range(10):
        lines.append(f"{datetime.now().strftime('%d.%m.%Y')},Kunde_{i},{random.randint(100, 10000)},OK")
    
    filepath.write_text("\n".join(lines), encoding='utf-8')


if __name__ == "__main__":
    from app.core.config import MOCK_DATA_DIR
    generate_mock_data(MOCK_DATA_DIR, num_customers=5, files_per_customer=50)
