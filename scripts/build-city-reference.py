"""Build the bundled public GeoNames index; no account or query data included."""
import argparse,gzip,hashlib,io,json,sqlite3,subprocess,tempfile,zipfile
from pathlib import Path
from datetime import date
import httpx

root=Path(__file__).resolve().parent.parent
source_url='https://download.geonames.org/export/dump/cities500.zip'
parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path);args=parser.parse_args()
raw=args.source.read_bytes() if args.source else httpx.get(source_url,timeout=90).raise_for_status().content
admin=httpx.get('https://download.geonames.org/export/dump/admin1CodesASCII.txt',timeout=30).raise_for_status().text
regions={line.split('\t')[0]:line.split('\t')[1] for line in admin.splitlines() if '\t' in line}
with tempfile.TemporaryDirectory(prefix='drg-city-reference-') as temporary:
 dbpath=Path(temporary)/'cities.sqlite';db=sqlite3.connect(dbpath)
 db.execute('CREATE TABLE cities(id INTEGER PRIMARY KEY,name TEXT,aliases TEXT,latitude REAL,longitude REAL,country_code TEXT,region TEXT,timezone TEXT,population INTEGER)')
 codes=set();rows=[]
 with zipfile.ZipFile(io.BytesIO(raw)) as archive:
  with archive.open('cities500.txt') as file:
   for line in io.TextIOWrapper(file,encoding='utf-8'):
    f=line.rstrip('\n').split('\t')
    aliases=list(dict.fromkeys([f[1],f[2],*f[3].split(',')]))
    aliases=[a for a in aliases if a and len(a)<=200]
    codes.add(f[8]);rows.append((int(f[0]),f[1],json.dumps(aliases,ensure_ascii=False),float(f[4]),float(f[5]),f[8],regions.get(f[8]+'.'+f[10],''),f[17],int(f[14] or 0)))
 db.executemany('INSERT INTO cities VALUES(?,?,?,?,?,?,?,?,?)',rows)
 db.execute("CREATE VIRTUAL TABLE names USING fts5(aliases,content='cities',content_rowid='id',tokenize='unicode61 remove_diacritics 2')")
 db.execute("INSERT INTO names(names) VALUES('rebuild')")
 country_js="const codes=JSON.parse(process.argv[1]); console.log(JSON.stringify(Object.fromEntries(codes.map(c=>[c,Object.fromEntries(['ru','en'].map(l=>[l,new Intl.DisplayNames([l],{type:'region'}).of(c)]))]))));"
 countries=json.loads(subprocess.check_output(['node','-e',country_js,json.dumps(sorted(codes))]))
 db.execute('CREATE TABLE countries(code TEXT PRIMARY KEY,ru TEXT,en TEXT)')
 db.executemany('INSERT INTO countries VALUES(?,?,?)',[(c,d['ru'],d['en']) for c,d in countries.items()])
 db.commit();db.execute('VACUUM');db.close()
 rawdb=dbpath.read_bytes();output=root/'app/geodata';output.mkdir(exist_ok=True)
 (output/'cities.sqlite.gz').write_bytes(gzip.compress(rawdb,compresslevel=9,mtime=0))
 metadata={'source':source_url,'date':date.today().isoformat(),'license':'CC BY 4.0 — GeoNames','source_sha256':hashlib.sha256(raw).hexdigest(),'database_sha256':hashlib.sha256(rawdb).hexdigest(),'cities':len(rows),'uncompressed_bytes':len(rawdb)}
 (output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(metadata));print('Compressed bytes:',(output/'cities.sqlite.gz').stat().st_size)
