from account.models import JournalLine

# Find all corrupted lines and sync them to their parent's description
bad_lines = JournalLine.objects.filter(description__in=['Debit leg', 'Credit leg'])

for line in bad_lines:
    line.description = line.journal_entry.description
    line.save()

print(f"Fixed {bad_lines.count()} legacy database rows!")
exit()
