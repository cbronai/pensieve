"""
pensieve-langchain — LangChain memory integration
==================================================

Drop-in BaseChatMemory replacement backed by Pensieve's SQLite hierarchical store.

Usage
-----
    from pensieve.langchain import PensieveMemory
    from langchain_anthropic import ChatAnthropic
    from langchain.chains import ConversationChain

    memory = PensieveMemory(user_id="alice")

    chain = ConversationChain(
        llm=ChatAnthropic(model="claude-opus-4-6"),
        memory=memory,
    )
    reply = chain.predict(input="What's my name?")

    # Or with LangChain Expression Language (LCEL):
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_core.runnables import RunnableWithMessageHistory

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant."),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{input}"),
    ])
    chain = prompt | ChatAnthropic(model="claude-opus-4-6")
    chain_with_memory = RunnableWithMessageHistory(
        chain,
        lambda session_id: PensieveMemory(user_id=session_id),
        input_messages_key="input",
        history_messages_key="history",
    )
"""

from .memory import PensieveMemory

__all__ = ["PensieveMemory"]
