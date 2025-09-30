# Quick script to print the educational markdown prompt (for manual verification)
# Run from repository root: 
# powershell> $env:PYTHONPATH='.'; python scripts/print_mermaid_prompt.py

from app.services.material_processing_service.handle_material_processing import _generate_educational_markdown_prompt

if __name__ == '__main__':
    print(_generate_educational_markdown_prompt(3))
