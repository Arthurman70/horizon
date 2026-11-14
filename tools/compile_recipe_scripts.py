#!/usr/bin/env python3
"""Translate the declarative CraftTweaker subset used by Modders Reformed.

compile_recipe_scripts.py SCRIPTS NEW_OUTPUT
Produces a private data pack and a sibling index of removal rules. The caller
applies removal rules to the carried base recipes before mounting the pack.
Custom machine recipes need their matching serializer at runtime.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import tempfile

ID = r'[a-z0-9_.-]+:[a-z0-9_./-]+'
STACK = re.compile(rf'<(item|tag:item):({ID})>(?:\s*\*\s*([0-9]+))?')


def split_args(text):
    fields, start, depth = [], 0, 0
    for idx, char in enumerate(text):
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
        elif char == ',' and depth == 0:
            fields.append(text[start:idx].strip())
            start = idx + 1
        if depth < 0:
            raise ValueError('Unbalanced list')
    if depth != 0:
        raise ValueError('Unbalanced list')
    return fields + [text[start:].strip()]


def sequence(text):
    if not text.startswith('[') or not text.endswith(']'):
        raise ValueError(f'Expected list: {text}')
    elements = split_args(text[1:-1])
    if elements[-1] == '':
        elements.pop()
    return elements


def stack(text):
    match = STACK.fullmatch(text)
    if not match or match[1] != 'item' or not 1 <= int(match[3] or 1) <= 99:
        raise ValueError(f'Expected item stack: {text}')
    return {'id': match[2], 'count': int(match[3] or 1)}


def ingredient(text):
    match = STACK.fullmatch(text)
    if not match or match[3]:
        raise ValueError(f'Expected ingredient: {text}')
    return ('#' if match[1]=='tag:item' else '') + match[2]


def translate(folder):
    recipes, tags, removals, actions = {}, {}, [], Counter()
    for path in sorted(folder.glob('*.zs')):
        source = re.sub(r'//[^\n]*','',path.read_text(encoding='utf-8-sig'))
        for statement in filter(str.strip,source.split(';')):
            match = re.fullmatch(r'\s*([^\s(]+)\((.*)\)\s*',statement,re.S)
            if not match:
                raise ValueError(f'Unsupported statement in {path.name}: {statement}')
            op, args = match[1], split_args(match[2])
            actions[op] += 1
            if op == 'craftingTable.remove':
                removals.append(dict(kind='crafted_output', item=stack(args[0])['id']))
                continue
            if op == '<recipetype:farmersdelight:cutting>.removeRecipe':
                removals.append(dict(kind='cutting_outputs', stacks=list(map(stack,sequence(args[0])))))
                continue
            tag = re.fullmatch(rf'<tag:item:({ID})>\.add',op)
            if tag:
                tag_path = tag[1].split(':',1)[1]
                if any(not part or part in ('.','..') for part in tag_path.split('/')) or tag[1].split(':',1)[0] in ('.','..'):
                    raise ValueError(f'Invalid tag path in {path.name}')
                tags.setdefault(tag[1],[]).extend(map(ingredient,sequence(args[0])))
                continue
            name = json.loads(args[0])
            if not isinstance(name,str) or not re.fullmatch('[a-z0-9_]+',name):
                raise ValueError(f'Unsupported recipe ID: {name}')
            if op == 'craftingTable.removeByName':
                recipes.pop(name,None)
                removals.append(dict(kind='id', id='crafttweaker:'+name))
                continue
            if name in recipes:
                raise ValueError(f'Duplicate recipe: {name}')
            if op == 'craftingTable.addShapeless':
                rec = dict(type='minecraft:crafting_shapeless', ingredients=list(map(ingredient,sequence(args[2]))),result=stack(args[1]))
            elif op == 'craftingTable.addShaped':
                rows = [list(map(ingredient,sequence(row))) for row in sequence(args[2])]
                if not 1<=len(rows)<=3 or not 1<=len(rows[0])<=3 or any(len(row)!=len(rows[0]) for row in rows):
                    raise ValueError(f'Invalid shaped recipe: {name}')
                keys = {v:chr(65+i) for i,v in enumerate(dict.fromkeys(v for row in rows for v in row))}
                rec = dict(type='minecraft:crafting_shaped',pattern=[''.join(keys[v] for v in row) for row in rows],key={k:v for v,k in keys.items()},result=stack(args[1]))
            elif op == 'furnace.addRecipe':
                rec = dict(type='minecraft:smelting',ingredient=ingredient(args[2]),result=stack(args[1]),experience=float(args[3]),cookingtime=int(args[4]))
            elif op == '<recipetype:farmersdelight:cutting>.addRecipe':
                tool = ingredient(args[3])
                rec = dict(type='farmersdelight:cutting', ingredients=[{'item':stack(args[1])['id']}],result=[{'item':stack(x)} for x in sequence(args[2])],tool=({'tag':tool[1:]} if tool.startswith('#') else {'item':tool}))
            elif op == '<recipetype:create:pressing>.addRecipe':
                rec = dict(type='create:pressing', ingredients=[ingredient(args[2])],results=list(map(stack,sequence(args[1]))),processing_time=int(args[3]))
            else:
                raise ValueError(f'Unsupported operation in {path.name}: {op}')
            recipes[name] = rec
    if not recipes:
        raise ValueError('No recipes found')
    return recipes,tags,removals,actions


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scripts',type=Path)
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    recipes,tags,removals,actions = translate(args.scripts)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():
        raise FileExistsError(args.output)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as tmp:
        root = Path(tmp)/'result'
        pack = root/'pack'
        write(pack/'pack.mcmeta',dict(pack=dict(description='Modders Reformed recipes',min_format=121,max_format=121,pack_format=121)))
        for name,recipe in recipes.items():
            write(pack/'data/crafttweaker/recipe'/f'{name}.json',recipe)
        for tag,values in tags.items():
            ns,name = tag.split(':')
            write(pack/'data'/ns/'tags/item'/f'{name}.json',dict(replace=False,values=values))
        write(root/'index.json',dict(actions=actions,removals=removals,recipes=len(recipes),tags=len(tags)))
        root.rename(args.output)


if __name__=='__main__':
    main()
