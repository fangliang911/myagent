import copy
import os
import re

import json
from config_utils import get_user_cfg_file
from modelscope_agent.prompt.prompt import (KNOWLEDGE_INTRODUCTION_PROMPT,
                                            KNOWLEDGE_PROMPT, LengthConstraint,
                                            PromptGenerator, build_raw_prompt)

from modelscope.utils.config import Config

DEFAULT_SYSTEM_TEMPLATE = """

# Tools

## You have the following tools:

<tool_list>

## When you need to call a tool, please intersperse the following tool command in your reply. %s

Tool Invocation
Action: The name of the tool, must be one of <tool_name_list>
Action Input: Tool input
Observation: <result>Tool returns result</result>
Answer: Summarize the results of this tool call based on Observation. If the result contains url, please do not show it.

```
[Link](url)
```

# Instructions
""" % 'You can call zero or more times according to your needs:'

DEFAULT_SYSTEM_TEMPLATE_WITHOUT_TOOL = """

# Instructions
"""

DEFAULT_INSTRUCTION_TEMPLATE = ''

DEFAULT_USER_TEMPLATE = (
    '(You are playing as <role_name>, you can use tools: <tool_name_list><knowledge_note>)<file_names><user_input>'
)

DEFAULT_USER_TEMPLATE_WITHOUT_TOOL = """(You are playing as <role_name><knowledge_note>) <file_names><user_input>"""

DEFAULT_EXEC_TEMPLATE = """Observation: <result><exec_result></result>\nAnswer:"""

TOOL_DESC = (
    '{name_for_model}: {name_for_human} API. {description_for_model} Input parameters: {parameters}'
)


class CustomPromptGenerator(PromptGenerator):

    def __init__(
            self,
            system_template=DEFAULT_SYSTEM_TEMPLATE,
            instruction_template=DEFAULT_INSTRUCTION_TEMPLATE,
            user_template=DEFAULT_USER_TEMPLATE,
            exec_template=DEFAULT_EXEC_TEMPLATE,
            assistant_template='',
            sep='\n\n',
            llm=None,
            length_constraint=LengthConstraint(),
            tool_desc=TOOL_DESC,
            default_user_template_without_tool=DEFAULT_USER_TEMPLATE_WITHOUT_TOOL,
            default_system_template_without_tool=DEFAULT_SYSTEM_TEMPLATE_WITHOUT_TOOL,
            addition_assistant_reply='OK.',
            **kwargs):

        # hack here for special prompt, such as add an addition round before user input
        self.add_addition_round = kwargs.get('add_addition_round', False)
        self.addition_assistant_reply = addition_assistant_reply
        builder_cfg_file = get_user_cfg_file(
            uuid_str=kwargs.get('uuid_str', ''))
        builder_cfg = Config.from_file(builder_cfg_file)
        self.builder_cfg = builder_cfg
        self.knowledge_file_name = kwargs.get('knowledge_file_name', '')
        if not len(instruction_template):
            instruction_template = self._parse_role_config(builder_cfg)

        self.llm = llm
        self.prompt_preprocessor = build_raw_prompt(llm.model_id)
        self.length_constraint = length_constraint
        self._parse_length_restriction()

        self.tool_desc = tool_desc
        self.default_user_template_without_tool = default_user_template_without_tool
        self.default_system_template_without_tool = default_system_template_without_tool

        super().__init__(
            system_template=system_template,
            instruction_template=instruction_template,
            user_template=user_template,
            exec_template=exec_template,
            assistant_template=assistant_template,
            sep=sep,
            llm=llm,
            length_constraint=length_constraint)

    def _parse_role_config(self, config: dict):
        prompt = 'You are playing as an AI-Agent, '

        # concat prompt
        if 'name' in config and config['name']:
            prompt += ('Your name is ' + config['name'] + '.')
        if 'description' in config and config['description']:
            prompt += config['description']
        prompt += '\nYou have the following specific functions:'

        if 'instruction' in config and config['instruction']:
            if isinstance(config['instruction'], list):
                for ins in config['instruction']:
                    prompt += ins
                    prompt += '；'
            elif isinstance(config['instruction'], str):
                prompt += config['instruction']
            if prompt[-1] == '；':
                prompt = prompt[:-1]

        prompt += '\nNow you will start playing as'
        if 'name' in config and config['name']:
            prompt += config['name']
        prompt += ', say "OK." if you understand, do not say anything else.'

        return prompt

    def _parse_length_restriction(self):
        constraint = self.llm.cfg.get('length_constraint', None)
        # if isinstance(constraint, Config):
        #     constraint = constraint.to_dict()
        self.length_constraint.update(constraint)

    def _update_user_prompt_without_knowledge(self, task, tool_list, **kwargs):
        if len(tool_list) > 0:
            # user input
            user_input = self.user_template.replace('<role_name>',
                                                    self.builder_cfg.name)
            user_input = user_input.replace(
                '<tool_name_list>',
                ','.join([tool.name for tool in tool_list]))
        else:
            self.user_template = self.default_user_template_without_tool
            user_input = self.user_template.replace('<user_input>', task)
            user_input = user_input.replace('<role_name>',
                                            self.builder_cfg.name)

        user_input = user_input.replace('<user_input>', task)

        if 'append_files' in kwargs:
            append_files = kwargs.get('append_files', [])

            # remove all files that should add to knowledge
            # exclude_extensions = {".txt", ".md", ".pdf"}
            # filtered_files = [file for file in append_files if
            #                   not any(file.endswith(ext) for ext in exclude_extensions)]

            if len(append_files) > 0:
                file_names = ','.join(
                    [os.path.basename(path) for path in append_files])
                user_input = user_input.replace('<file_names>',
                                                f'[上传文件{file_names}]')
            else:
                user_input = user_input.replace('<file_names>', '')
        else:
            user_input = user_input.replace('<file_names>', '')

        return user_input

    def _get_knowledge_template(self):
        return '. Please read the knowledge base at the beginning.'

    def init_prompt(self, task, tool_list, knowledge_list, **kwargs):

        if len(self.history) == 0:

            self.history.append({
                'role': 'system',
                'content': 'You are a helpful assistant.'
            })

            if len(tool_list) > 0:
                prompt = f'{self.system_template}\n{self.instruction_template}'

                # get tool description str
                tool_str = self.get_tool_str(tool_list)
                prompt = prompt.replace('<tool_list>', tool_str)

                tool_name_str = self.get_tool_name_str(tool_list)
                prompt = prompt.replace('<tool_name_list>', tool_name_str)
            else:
                self.system_template = self.default_system_template_without_tool
                prompt = f'{self.system_template}\n{self.instruction_template}'

            user_input = self._update_user_prompt_without_knowledge(
                task, tool_list, **kwargs)

            if len(knowledge_list) > 0:
                user_input = user_input.replace('<knowledge_note>',
                                                self._get_knowledge_template())
            else:
                user_input = user_input.replace('<knowledge_note>', '')

            self.system_prompt = copy.deepcopy(prompt)

            # build history
            if self.add_addition_round:
                self.history.append({
                    'role': 'user',
                    'content': self.system_prompt
                })
                self.history.append({
                    'role': 'assistant',
                    'content': self.addition_assistant_reply
                })
                self.history.append({'role': 'user', 'content': user_input})
                self.history.append({
                    'role': 'assistant',
                    'content': self.assistant_template
                })
            else:
                self.history.append({
                    'role': 'user',
                    'content': self.system_prompt + user_input
                })
                self.history.append({
                    'role': 'assistant',
                    'content': self.assistant_template
                })

            self.function_calls = self.get_function_list(tool_list)
        else:
            user_input = self._update_user_prompt_without_knowledge(
                task, tool_list, **kwargs)
            if len(knowledge_list) > 0:
                user_input = user_input.replace('<knowledge_note>',
                                                self._get_knowledge_template())
            else:
                user_input = user_input.replace('<knowledge_note>', '')

            self.history.append({'role': 'user', 'content': user_input})
            self.history.append({
                'role': 'assistant',
                'content': self.assistant_template
            })

        if len(knowledge_list) > 0:
            knowledge_str = self.get_knowledge_str(
                knowledge_list,
                file_name=self.knowledge_file_name,
                only_content=True)
            self.update_knowledge_str(knowledge_str)

    def _get_tool_template(self):
        return '\n\n# Tools\n\n'

    def update_knowledge_str(self, knowledge_str):
        """If knowledge base information was not used previously, it will be added;
        if knowledge base information was previously used, it will be replaced.

        Args:
            knowledge_str (str): knowledge str generated by get_knowledge_str
        """
        knowledge_introduction = KNOWLEDGE_INTRODUCTION_PROMPT.replace(
            '<file_name>', self.knowledge_file_name)
        if len(knowledge_str) > self.length_constraint.knowledge:
            # todo: use tokenizer to constrain length
            knowledge_str = knowledge_str[-self.length_constraint.knowledge:]
        knowledge_str = f'{KNOWLEDGE_PROMPT}{self.sep}{knowledge_introduction}{self.sep}{knowledge_str}'

        for i in range(0, len(self.history)):
            if self.history[i]['role'] == 'user':
                content: str = self.history[i]['content']
                start_pos = content.find(f'{KNOWLEDGE_PROMPT}{self.sep}')
                end_pos = content.rfind(self._get_tool_template())
                if start_pos >= 0 and end_pos >= 0:  # replace knowledge

                    self.history[i]['content'] = content[
                        0:start_pos] + knowledge_str + content[end_pos:]
                    break
                elif start_pos < 0 and end_pos == 0:  # add knowledge
                    self.history[i]['content'] = knowledge_str + content
                    break
                else:
                    continue

    def get_tool_str(self, tool_list):
        tool_texts = []
        for tool in tool_list:
            tool_texts.append(
                self.tool_desc.format(
                    name_for_model=tool.name,
                    name_for_human=tool.name,
                    description_for_model=tool.description,
                    parameters=json.dumps(tool.parameters,
                                          ensure_ascii=False)))
            # + ' ' + FORMAT_DESC['json'])
        tool_str = '\n\n'.join(tool_texts)
        return tool_str

    def get_tool_name_str(self, tool_list):
        tool_name = []
        for tool in tool_list:
            tool_name.append(tool.name)

        tool_name_str = json.dumps(tool_name, ensure_ascii=False)
        return tool_name_str

    def _generate(self, llm_result, exec_result: str):
        """
        generate next round prompt based on previous llm_result and exec_result and update history
        """
        if len(llm_result) != 0:
            self.history[-1]['content'] += f'{llm_result}'
        if len(exec_result) != 0:
            # handle image markdown wrapper
            image_markdown_re = re.compile(
                pattern=r'!\[IMAGEGEN\]\(([\s\S]+)\)')
            match = image_markdown_re.search(exec_result)
            if match is not None:
                exec_result = match.group(1).rstrip()
            exec_result = self.exec_template.replace('<exec_result>',
                                                     str(exec_result))
            self.history[-1]['content'] += exec_result

        # generate plate prompt here
        self.prompt = self.prompt_preprocessor(self.history)
        return self.prompt
