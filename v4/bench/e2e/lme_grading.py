"""LongMemEval official question-type templates, content-pinned locally.

Source: xiaowu0162/LongMemEval/src/evaluation/evaluate_qa.py,
get_anscheck_prompt, accessed 2026-10-11. MIT, Copyright (c) 2024 Di Wu.
Only the prompt function is reproduced; no OpenAI/API client is imported.
The upstream Git revision was unavailable: do not claim a revision attestation.
The vendored templates and provenance are sealed by the campaign source manifest.
"""
import ast
from pathlib import Path

SOURCE = 'https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py'
TYPES = frozenset({'single-session-user', 'single-session-assistant', 'multi-session',
                   'temporal-reasoning', 'knowledge-update', 'single-session-preference'})


def get_anscheck_prompt(task, question, answer, response, abstention=False):
    if not abstention:
        if task in ['single-session-user', 'single-session-assistant', 'multi-session']:
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'temporal-reasoning':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'knowledge-update':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'single-session-preference':
            template = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        else:
            raise NotImplementedError(task)
    else:
        template = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."
        prompt = template.format(question, answer, response)
    return prompt


def judge_payload(payload):
    if 'official_prompt' in payload:
        return payload['official_prompt']
    return get_anscheck_prompt(payload['question_type'], payload['question'], payload['reference'],
                               payload['candidate'], abstention=payload['abstention'])


def source_templates(path):
    """Read official string literals only: never import/execute its API client.

    No normalization/rewording of literals, including continuation whitespace.
    The five branches are fixed by the audited official function's order.
    """
    path = Path(path)
    if path.stat().st_size > 20000:
        raise ValueError('Official evaluator exceeds 20 KB bound')
    tree = ast.parse(path.read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'get_anscheck_prompt']
    if len(functions) != 1:
        raise ValueError('Official grading function missing/ambiguous')
    function = functions[0]
    assignments = sorted((n for n in ast.walk(function) if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'template' for t in n.targets)),
                         key=lambda n: n.lineno)
    names = ['base', 'temporal-reasoning', 'knowledge-update', 'single-session-preference', 'abstention']
    if len(assignments) != len(names) or any(not isinstance(n.value, ast.Constant) or not isinstance(n.value.value, str) for n in assignments):
        raise ValueError('Unknown official template structure; review before inference')
    values = {name: n.value.value for name, n in zip(names, assignments)}
    for name, template in values.items():
        expected = get_anscheck_prompt('single-session-user' if name == 'base' else name, 'Q', 'A', 'R', name == 'abstention')
        # Verify semantic text against the audited snapshot; preserve upstream
        # continuation whitespace in the actual prompts, never the normalized copy.
        if ''.join(template.format('Q', 'A', 'R').split()) != ''.join(expected.split()):
            raise ValueError('Official grader changed; protocol amendment required')
    return values


def official_prompt(templates, task, question, answer, response, abstention):
    if task not in TYPES:
        raise ValueError('Unknown question type')
    key = 'abstention' if abstention else ('base' if task in {'single-session-user', 'single-session-assistant', 'multi-session'} else task)
    return templates[key].format(question, answer, response)
