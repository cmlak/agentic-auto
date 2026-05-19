import os
from django.core.management.base import BaseCommand, CommandError
import fitz  # PyMuPDF


class Command(BaseCommand):
    help = "Normalizes page orientation to reliably mask out the typo and inject the corrected text."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            type=str,
            default="speedtech.pdf",
            help="Input PDF filename",
        )
        parser.add_argument(
            "--output",
            type=str,
            default="speedtech_fixed.pdf",
            help="Output PDF filename",
        )

    def handle(self, *args, **options):
        base_url = r"C:\bakertilly\BakerTilly\CCKT"
        
        input_path = os.path.join(base_url, options["input"])
        output_path = os.path.join(base_url, options["output"])

        if not os.path.exists(input_path):
            raise CommandError(f"Input file not found at: {input_path}")

        try:
            self.stdout.write(self.style.NOTICE(f"Opening document: {input_path}"))
            doc = fitz.open(input_path)
            
            page_index = 9 if len(doc) >= 10 else 0
            page = doc[page_index]

            # 1. Store the original internal rotation metadata, then reset it to 0
            # This forces the internal canvas grid to behave normally
            original_rotation = page.rotation
            page.set_rotation(0)
            
            self.stdout.write(
                self.style.NOTICE(f"Canvas normalized. Dimensions: {page.rect.width} x {page.rect.height}")
            )

            # 2. Define standard coordinates based on normalized dimensions
            # On a standard upright A4 template page (~595 x 842):
            # The acceptance section sits roughly between Y=400 and Y=550.
            X1 = 30   # Left margin boundary
            Y1 = 490  # Top boundary (reaches above "SPEECHTECH")
            X2 = 450  # Right margin boundary (wide enough to clear the full string)
            Y2 = 525  # Bottom boundary (reaches below "SPEECHTECH")

            rect_typo = fitz.Rect(X1, Y1, X2, Y2)
            text_insertion_point = fitz.Point(X1, Y2 - 10)

            # 3. Draw the solid white block over the text coordinates
            page.draw_rect(rect_typo, color=(1, 1, 1), fill=(1, 1, 1))

            # 4. Insert the correct text string cleanly onto the canvas
            page.insert_text(
                text_insertion_point, 
                "SPEEDTECH INDUSTRIAL CO., LTD.", 
                fontsize=12, 
                fontname="helv",
                set_simple=True
            )

            # 5. Restore the original orientation metadata so the document keeps its expected viewing angle
            if original_rotation != 0:
                page.set_rotation(original_rotation)

            # Save out the structural changes
            doc.save(output_path)
            doc.close()
            
            self.stdout.write(
                self.style.SUCCESS(f"Successfully applied visual mask! Saved to: {output_path}")
            )

        except Exception as e:
            raise CommandError(f"Execution error while processing canvas layers: {str(e)}")