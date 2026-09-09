"""Render assistant-authored reference drafts from source labels, never retrieval outputs."""
import hashlib,json
from pathlib import Path
DATA=Path('data/eval/ecommerce-complex-v2')
OUT=Path('data/eval/ecommerce-answer-reference-v1')

def build():
    inputs={r['id']:r for r in json.loads((DATA/'heldout.inputs.json').read_text())}
    labels=json.loads((DATA/'heldout.gold.json').read_text())
    docs={r['source_id']:r['content'] for r in json.loads((DATA/'corpus.json').read_text())}
    notes=json.loads((OUT/'author-notes.json').read_text())
    assert set(notes)=={r['group_id'] for r in labels}
    answers=[]
    md=['# 80题纯RAG参考答案初稿','',
        '由助手根据问题、用户历史和模拟知识原文编写；未读取检索排名、系统答案或verifier结果。用于后续评分参考，不是被测系统输出，不计答案准确率。原模拟政策及本稿均未获独立人工语义审定。允许等义回答，不以文字完全一致作为正确标准。',
        '', '全部问题按题目指定的2026年6月1日起中国大陆官网规则回答。E1/E2/E3为每题局部引用，JSON中保存原文及准确位置。未知前提不自动补齐，假设不当成实际发生。','']
    for label in labels:
        case=inputs[label['id']];note=notes[label['group_id']];category=label['category']
        if category=='missing_conditions':
            lead='目前不能直接认定您满足条件或该操作可行。需要先核实：'+note['check']+'。'
            scenario_rule='必须保留前提未知，不直接判定用户符合资格或已经执行操作；应列出待核实条件。'
        elif category=='hypothetical':
            lead='以下仅按前文假设说明，不表示这些情况已实际发生。在该假设下：'+note['conclusion']
            scenario_rule='不得将假设条件升级为用户实际事实，也不得声称查过订单或执行过操作。'
        else:
            lead=('按您更正后的'+label['topic']+'问题说明：' if category=='correction' else '')+note['conclusion']
            scenario_rule='结论只能使用当前问题及用户历史明确提供的前提；未知条件仍须保留。'
        sections=[];evidence=[];requirements=[]
        for index,unit in enumerate(label['evidence_units'],1):
            assert docs[unit['source_id']][unit['start_char']:unit['end_char']]==unit['quote']
            ref='E'+str(index)
            sections.append(unit['quote']+' ['+ref+']')
            evidence.append({'citation_id':ref,**unit})
            requirements.append({'requirement_id':unit['unit_id'],'source_text':unit['quote'],'evidence_refs':[ref],'grading_note':'核对命题、限定条件、例外、否定及步骤关系；允许等义表述，不用字符串匹配。'})
        answer=lead+'\n\n'+'\n\n'.join(sections)
        if category!='missing_conditions':
            answer+='\n\n'+('如要判断某个实际情形，仍需核实：' if category=='hypothetical' else '具体判断还需核对：')+note['check']+'。'
        row={'id':case['id'],'group_id':label['group_id'],'category':category,'question':case['message'],'history':case['history'],
             'reference_answer_draft':answer,'required_evidence_points':requirements,'scenario_requirement':scenario_rule,
             'unsupported_inferences_to_avoid':note['avoid'],'evidence':evidence,
             'review_status':'assistant_authored_draft_pending_independent_semantic_review'}
        answers.append(row)
        md+=['## '+case['id']+' · '+label['topic']+' · '+category,'']
        if case['history']:
            md+=['历史：','']+[f'- {role}：{text}' for role,text in case['history']]+['']
        md+=['问题：'+case['message'],'','参考初稿：','',answer,'','审阅重点：'+scenario_rule+note['avoid'],'']
    assert len(answers)==80 and len({r['id'] for r in answers})==80
    (OUT/'heldout.references.json').write_text(json.dumps(answers,ensure_ascii=False,indent=2)+'\n')
    (OUT/'heldout.references.zh-CN.md').write_text('\n'.join(md).rstrip()+'\n')
    paths=[DATA/'heldout.inputs.json',DATA/'heldout.gold.json',DATA/'corpus.json',OUT/'author-notes.json',OUT/'heldout.references.json',OUT/'heldout.references.zh-CN.md']
    manifest={'answers':80,'rule_families':20,'source_links_verified':240,'api_calls':0,'author':'Codex assistant; authored scenario interpretations plus verbatim policy clauses','semantic_review':'pending independent review','evaluation_result':None,'inputs_and_outputs':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='inputs_and_outputs'},ensure_ascii=False))

if __name__=='__main__':build()
