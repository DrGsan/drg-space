"""Bounded formula interpreter. No eval, imports, attribute access or Python execution."""
import ast
import calendar
import io
import math
import re
import tokenize
from functools import lru_cache
from datetime import date,timedelta

class FormulaError(ValueError):pass

def number(value):
    try:result=float(value or 0)
    except (TypeError,ValueError):raise FormulaError('Нужны числовые значения.') from None
    if not math.isfinite(result) or abs(result)>1e15:raise FormulaError('Число выходит за допустимые пределы.')
    return result

def bounded(value):
    if isinstance(value,complex):raise FormulaError('Результат не является вещественным числом.')
    if isinstance(value,(int,float)) and not isinstance(value,bool):number(value)
    if isinstance(value,str) and len(value)>5000:raise FormulaError('Слишком длинный результат.')
    if isinstance(value,list) and len(value)>1000:raise FormulaError('Слишком много элементов результата.')
    return value

def as_date(value):
    if isinstance(value,dict):value=value.get('start')
    try:return date.fromisoformat(value)
    except (ValueError,TypeError):raise FormulaError('Нужна дата в формате ГГГГ-ММ-ДД.') from None

def date_add(value,amount,unit):
    start=as_date(value);amount=int(number(amount))
    if abs(amount)>100000:raise FormulaError('Слишком большое смещение даты.')
    try:
        if unit in ('days','day'):return (start+timedelta(days=amount)).isoformat()
        if unit in ('weeks','week'):return (start+timedelta(days=amount*7)).isoformat()
        if unit in ('months','month','years','year'):
            n=start.year*12+start.month-1+amount*(12 if unit in ('years','year') else 1);year,month=divmod(n,12);month+=1
            return start.replace(year=year,month=month,day=min(start.day,calendar.monthrange(year,month)[1])).isoformat()
    except (ValueError,OverflowError):raise FormulaError('Дата выходит за допустимые пределы.') from None
    raise FormulaError('Единицы даты: days, weeks, months, years.')

def date_between(a,b,unit):
    days=(as_date(a)-as_date(b)).days
    if unit in ('days','day'):return days
    if unit in ('weeks','week'):return int(days/7)
    if unit in ('months','month'):return (as_date(a).year-as_date(b).year)*12+as_date(a).month-as_date(b).month
    if unit in ('years','year'):return as_date(a).year-as_date(b).year
    raise FormulaError('Неизвестная единица даты.')

def format_date(value,pattern):
    d=as_date(value)
    return re.sub(r'YYYY|YY|MM|DD',lambda m:{'YYYY':str(d.year).zfill(4),'YY':str(d.year%100).zfill(2),'MM':str(d.month).zfill(2),'DD':str(d.day).zfill(2)}[m[0]],str(pattern))

FUNCTIONS={
 'abs':lambda x:abs(number(x)),'round':lambda x,n=0:round(number(x),max(-10,min(10,int(number(n))))),
 'ceil':lambda x:math.ceil(number(x)),'floor':lambda x:math.floor(number(x)),
 'min':lambda *x:min(number(v) for v in (x[0] if len(x)==1 and isinstance(x[0],list) else x)),
 'max':lambda *x:max(number(v) for v in (x[0] if len(x)==1 and isinstance(x[0],list) else x)),
 'sum':lambda x:sum(number(v) for v in x),'average':lambda x:sum(number(v) for v in x)/len(x) if x else 0,
 'length':lambda x:len(x or []),'empty':lambda x:not x,'contains':lambda x,y:y in (x or []),
 'includes':lambda x,y:y in (x or []),'concat':lambda *x:''.join(str(v) for v in x),
 'format':lambda x:str(x),'toNumber':number,'lower':lambda x:str(x).lower(),'upper':lambda x:str(x).upper(),
 'replaceAll':lambda x,a,b:str(x).replace(str(a),str(b)) if str(a) else str(x),
 'join':lambda x,sep=', ':str(sep).join(str(v) for v in (x or [])),
 'first':lambda x:x[0] if x else None,'last':lambda x:x[-1] if x else None,
 'unique':lambda x:list(dict.fromkeys(x or [])),
 'dateAdd':date_add,'dateSubtract':lambda x,n,u:date_add(x,-number(n),u),'dateBetween':date_between,
 'dateStart':lambda x:x.get('start') if isinstance(x,dict) else x,
 'dateEnd':lambda x:x.get('end') or x.get('start') if isinstance(x,dict) else x,
 'parseDate':lambda x:as_date(x).isoformat(),'formatDate':format_date,
 'all_of':lambda *x:all(x),'any_of':lambda *x:any(x),'negate':lambda x:not x,
}
METHODS={'length','sum','average','join','first','last','unique','includes','contains','lower','upper'}

def parse(expression):
    if not isinstance(expression,str) or not expression.strip() or len(expression)>2000:raise FormulaError('Формула: от 1 до 2000 символов.')
    return parsed(expression)

@lru_cache(maxsize=2048)
def parsed(expression):
    try:
        tokens=list(tokenize.generate_tokens(io.StringIO(expression).readline));out=[];i=0
        while i<len(tokens):
            kind,value,*_=tokens[i]
            if value in ('&','|') and i+1<len(tokens) and tokens[i+1].string==value:
                out.append((tokenize.NAME,'and' if value=='&' else 'or'));i+=2;continue
            if value=='^':value='**'
            if kind==tokenize.NAME:
                if value in ('true','false'):value=value.capitalize()
                if value=='if':value='choose'
                if value in ('and','or','not') and i+1<len(tokens) and tokens[i+1].string=='(':value={'and':'all_of','or':'any_of','not':'negate'}[value]
            if value=='!':kind,value=tokenize.NAME,'not'
            out.append((kind,value));i+=1
        tree=ast.parse(tokenize.untokenize(out),mode='eval')
    except (SyntaxError,tokenize.TokenError,IndentationError):raise FormulaError('Проверьте синтаксис формулы.') from None
    if len(list(ast.walk(tree)))>200:raise FormulaError('Формула слишком сложная.')
    allowed=(ast.Expression,ast.Constant,ast.Call,ast.Name,ast.Load,ast.BinOp,ast.UnaryOp,ast.Compare,ast.BoolOp,ast.IfExp,ast.List,ast.Tuple,ast.Attribute,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow,ast.USub,ast.UAdd,ast.Not,ast.And,ast.Or,ast.Eq,ast.NotEq,ast.Gt,ast.GtE,ast.Lt,ast.LtE)
    for node in ast.walk(tree):
        if not isinstance(node,allowed):raise FormulaError('Недопустимая конструкция формулы.')
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name) and node.func.id=='prop' and (len(node.args)!=1 or not isinstance(node.args[0],ast.Constant) or not isinstance(node.args[0].value,str)):raise FormulaError('prop() принимает имя свойства в кавычках.')
            if node.keywords or len(node.args)>20:raise FormulaError('Некорректные аргументы.')
            if isinstance(node.func,ast.Name):
                if node.func.id not in {*FUNCTIONS,'prop','choose','now','today'}:raise FormulaError('Неизвестная функция: '+node.func.id)
            elif not isinstance(node.func,ast.Attribute) or node.func.attr not in METHODS:raise FormulaError('Метод недоступен.')
        if isinstance(node,ast.Attribute) and node.attr not in METHODS:raise FormulaError('Атрибут недоступен.')
        if isinstance(node,ast.Name) and node.id not in {*FUNCTIONS,'prop','choose','now','today'}:raise FormulaError('Неизвестное имя: '+node.id)
    return tree

def references(expression):
    tree=parse(expression);result=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='prop':
            if len(node.args)!=1 or not isinstance(node.args[0],ast.Constant) or not isinstance(node.args[0].value,str):raise FormulaError('prop() принимает имя свойства в кавычках.')
            result.append(node.args[0].value)
    return result

def evaluate(expression,get_property,today=None,budget=None):
    tree=parse(expression);budget=budget if budget is not None else [5000]
    def visit(node,depth=0):
        budget[0]-=1
        if depth>24 or budget[0]<0:raise FormulaError('Превышена сложность вычислений.')
        ev=lambda n:visit(n,depth+1)
        if isinstance(node,ast.Expression):return ev(node.body)
        if isinstance(node,ast.Constant):
            if not isinstance(node.value,(str,int,float,bool,type(None))):raise FormulaError('Недопустимое значение.')
            return bounded(node.value)
        if isinstance(node,(ast.List,ast.Tuple)):return [ev(x) for x in node.elts]
        if isinstance(node,ast.Call):
            name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr
            if name=='choose':
                if len(node.args)!=3:raise FormulaError('if() принимает условие и два результата.')
                return ev(node.args[1] if ev(node.args[0]) else node.args[2])
            if name=='prop':return get_property(ev(node.args[0]))
            if name in ('now','today'):
                if node.args:raise FormulaError('today() не принимает аргументов.')
                return (today or date.today()).isoformat()
            args=[ev(x) for x in node.args]
            if isinstance(node.func,ast.Attribute):args.insert(0,ev(node.func.value))
            try:return bounded(FUNCTIONS[name](*args))
            except (ValueError,TypeError,ZeroDivisionError,OverflowError,KeyError,IndexError):raise FormulaError('Некорректные аргументы функции '+name+'.') from None
        if isinstance(node,ast.IfExp):return ev(node.body if ev(node.test) else node.orelse)
        if isinstance(node,ast.BoolOp):
            result=True if isinstance(node.op,ast.And) else False
            for item in node.values:
                result=ev(item)
                if isinstance(node.op,ast.And) and not result or isinstance(node.op,ast.Or) and result:return result
            return result
        if isinstance(node,ast.UnaryOp):
            value=ev(node.operand)
            return not value if isinstance(node.op,ast.Not) else -number(value) if isinstance(node.op,ast.USub) else number(value)
        if isinstance(node,ast.Compare):
            left=ev(node.left)
            for operation,right_node in zip(node.ops,node.comparators):
                right=ev(right_node)
                try:ok=left==right if isinstance(operation,ast.Eq) else left!=right if isinstance(operation,ast.NotEq) else left>right if isinstance(operation,ast.Gt) else left>=right if isinstance(operation,ast.GtE) else left<right if isinstance(operation,ast.Lt) else left<=right
                except TypeError:raise FormulaError('Нельзя сравнить эти значения.') from None
                if not ok:return False
                left=right
            return True
        if isinstance(node,ast.BinOp):
            a,b=ev(node.left),ev(node.right)
            if isinstance(node.op,ast.Add) and isinstance(a,str) and isinstance(b,str):return bounded(a+b)
            a,b=number(a),number(b)
            try:
                if isinstance(node.op,ast.Pow) and abs(b)>20:raise FormulaError('Степень слишком большая.')
                result=a+b if isinstance(node.op,ast.Add) else a-b if isinstance(node.op,ast.Sub) else a*b if isinstance(node.op,ast.Mult) else a/b if isinstance(node.op,ast.Div) else a%b if isinstance(node.op,ast.Mod) else a**b
                return bounded(result)
            except (ZeroDivisionError,OverflowError):raise FormulaError('Деление на ноль или слишком большое число.') from None
        raise FormulaError('Недопустимое выражение.')
    return visit(tree)
