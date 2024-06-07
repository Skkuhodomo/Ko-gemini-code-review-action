#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#          http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
import json
import os
from typing import List

import click
import google.generativeai as genai
import requests
from loguru import logger


def check_required_env_vars():
    """Check required environment variables"""
    required_env_vars = [
        "GEMINI_API_KEY",
        "ACESS_TOKEN",
        "GITHUB_REPOSITORY",
        "GITHUB_PULL_REQUEST_NUMBER",
        "GIT_COMMIT_HASH",
    ]
    for required_env_var in required_env_vars:
        if os.getenv(required_env_var) is None:
            raise ValueError(f"{required_env_var} is not set")


def get_review_prompt(extra_prompt: str = "") -> str:
    """Get a prompt template"""
    template = f"""
    You are an excellent software engineer and security expert tasked with reviewing a pull request (PR) or part of a large PR.
    
    Please thoroughly review the differences in the provided PR and identify any issues. Your review should include:
    1. A review summary highlighting the overall quality and main concerns.
    2. Specific comments on issues or improvements needed, organized by file.
    
    For each identified issue or suggestion, include:
    - The name of the file.
    - A clear description of the problem or suggestion.
    - Recommendations for improvement.

    If relevant, prioritize security concerns and best coding practices in your review.

    In subsequent messages, you will receive the differences between the GitHub file codes for review. Ready?
    """
    return template


def get_summarize_prompt() -> str:
    """Get a prompt template for summarizing a pull request."""
    template = """
    Please provide a summary of the following pull request (PR).

    Focus on:
    1. Highlighting pressing issues.
    2. Providing specific code suggestions to improve the PR.

    Here's the content you need to summarize:
    """
    return template



def create_a_comment_to_pull_request(
        github_token: str,
        github_repository: str,
        pull_request_number: int,
        git_commit_hash: str,
        body: str):
    """Create a comment to a pull request"""
    headers = {
        "Accept": "application/vnd.github.v3.patch",
        "authorization": f"Bearer {github_token}"
    }
    data = {
        "body": body,
        "commit_id": git_commit_hash,
        "event": "COMMENT"
    }
    url = f"https://api.github.com/repos/{github_repository}/pulls/{pull_request_number}/reviews"
    response = requests.post(url, headers=headers, data=json.dumps(data))
    return response


def chunk_string(input_string: str, chunk_size) -> List[str]:
    """Chunk a string"""
    chunked_inputs = []
    for i in range(0, len(input_string), chunk_size):
        chunked_inputs.append(input_string[i:i + chunk_size])
    return chunked_inputs


def get_review(
        model: str,
        diff: str,
        extra_prompt: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
        prompt_chunk_size: int
):
    """Get a review"""
    # Chunk the prompt
    review_prompt = get_review_prompt(extra_prompt=extra_prompt)
    chunked_diff_list = chunk_string(input_string=diff, chunk_size=prompt_chunk_size)
    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 0,
        "max_output_tokens": 8192,
    }
    genai_model = genai.GenerativeModel(model_name=model,generation_config=generation_config,system_instruction=extra_prompt)
    # Get summary by chunk
    chunked_reviews = []
    for chunked_diff in chunked_diff_list:
        convo = genai_model.start_chat(history=[
            {
                "role": "user",
                "parts": [review_prompt]
            },
            {
                "role": "model",
                "parts": ["Ok"]
            },
        ])
        convo.send_message(chunked_diff)
        review_result = convo.last.text
        logger.debug(f"Response AI: {review_result}")
        chunked_reviews.append(review_result)
    # If the chunked reviews are only one, return it

    if len(chunked_reviews) == 1:
        return chunked_reviews, chunked_reviews[0]

    if len(chunked_reviews) == 0:
        summarize_prompt = "Say that you didn't find any relevant changes to comment on any file"
    else:
        summarize_prompt = get_summarize_prompt()

    chunked_reviews_join = "\n".join(chunked_reviews)
    convo = genai_model.start_chat(history=[])
    convo.send_message(summarize_prompt+"\n\n"+chunked_reviews_join)
    summarized_review = convo.last.text
    logger.debug(f"Response AI: {summarized_review}")
    return chunked_reviews, summarized_review


def format_review_comment(summarized_review: str, chunked_reviews: List[str]) -> str:
    """Format reviews"""
    if len(chunked_reviews) == 1:
        return summarized_review
    unioned_reviews = "\n".join(chunked_reviews)
    review = f"""<details>
    <summary>{summarized_review}</summary>
    {unioned_reviews}
    </details>
    """
    return review


@click.command()
@click.option("--diff", type=click.STRING, required=True, help="Pull request diff")
@click.option("--diff-chunk-size", type=click.INT, required=False, default=3500, help="Pull request diff")
@click.option("--model", type=click.STRING, required=False, default="gemini-3.5-turbo", help="Model")
@click.option("--extra-prompt", type=click.STRING, required=False, default="", help="Extra prompt")
@click.option("--temperature", type=click.FLOAT, required=False, default=0.1, help="Temperature")
@click.option("--max-tokens", type=click.INT, required=False, default=512, help="Max tokens")
@click.option("--top-p", type=click.FLOAT, required=False, default=1.0, help="Top N")
@click.option("--frequency-penalty", type=click.FLOAT, required=False, default=0.0, help="Frequency penalty")
@click.option("--presence-penalty", type=click.FLOAT, required=False, default=0.0, help="Presence penalty")
@click.option("--log-level", type=click.STRING, required=False, default="INFO", help="Presence penalty")
def main(
        diff: str,
        diff_chunk_size: int,
        model: str,
        extra_prompt: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        frequency_penalty: float,
        presence_penalty: float,
        log_level: str
):
    # Set log level
    logger.level(log_level)
    # Check if necessary environment variables are set or not
    check_required_env_vars()

    # Set the Gemini API key
    api_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=api_key)

    # Request a code review
    chunked_reviews, summarized_review = get_review(
        diff=diff,
        extra_prompt=extra_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty,
        prompt_chunk_size=diff_chunk_size
    )
    logger.debug(f"Summarized review: {summarized_review}")
    logger.debug(f"Chunked reviews: {chunked_reviews}")

    # Format reviews
    review_comment = format_review_comment(summarized_review=summarized_review,
                                           chunked_reviews=chunked_reviews)
    # Create a comment to a pull request
    create_a_comment_to_pull_request(
        github_token=os.getenv("GITHUB_TOKEN"),
        github_repository=os.getenv("GITHUB_REPOSITORY"),
        pull_request_number=int(os.getenv("GITHUB_PULL_REQUEST_NUMBER")),
        git_commit_hash=os.getenv("GIT_COMMIT_HASH"),
        body=review_comment
    )


if __name__ == "__main__":
    # pylint: disable=no-value-for-parameter
    main()
