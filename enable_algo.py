import re

path = '/home/trader/.wine/drive_c/Program Files/MetaTrader 5/Config/common.ini'
with open(path, 'rb') as f:
    content = f.read()

# Decode UTF-16 LE
text = content.decode('utf-16le')
print('Current [Experts] section:')
for line in text.splitlines():
    if 'Enabled' in line or 'Experts' in line or 'Allow' in line:
        print(' ', line)

# Replace Enabled=0 with Enabled=1 in [Experts]
if 'Enabled=0' in text:
    text = text.replace('Enabled=0', 'Enabled=1')
    with open(path, 'wb') as f:
        f.write(text.encode('utf-16le'))
    print('SUCCESS: Enabled=1 written to common.ini!')
else:
    print('Enabled=0 not found, check lines above.')

