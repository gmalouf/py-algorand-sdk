import base64
import json
import os
import unittest
import urllib
from pathlib import Path
from urllib.request import Request, urlopen

import parse
from behave import register_type  # pylint: disable=no-name-in-module
from behave import given, step, then, when
from glom import glom

from algosdk import (
    dryrun_results,
    encoding,
    error,
    mnemonic,
    source_map,
    transaction,
)

from algosdk.atomic_transaction_composer import (
    SimulateAtomicTransactionResponse,
)
from algosdk.error import AlgodHTTPError
from algosdk.testing.dryrun import DryrunTestCaseMixin
from algosdk.v2client import *
from algosdk.v2client.models import (
    Account,
    ApplicationLocalState,
    DryrunRequest,
    DryrunSource,
    SimulateRequest,
    SimulateTraceConfig,
)
from tests.steps.steps import algod_port, indexer_port
from tests.steps.steps import token as daemon_token


@parse.with_pattern(r".*")
def parse_string(text):
    return text


register_type(MaybeString=parse_string)


@parse.with_pattern(r"true|false|")
def parse_bool(value):
    if value not in ("true", "false", ""):
        raise ValueError("Unknown value for include_all: {}".format(value))
    return value == "true"


register_type(MaybeBool=parse_bool)


def validate_error(context, err):
    if context.expected_status_code != 200:
        if context.expected_status_code == 500:
            assert context.expected_mock_response["message"] == err.args[0], (
                context.expected_mock_response,
                err.args[0],
            )
        else:
            raise NotImplementedError(
                "test does not know how to validate status code "
                + context.expected_status_code
            )
    else:
        raise err


def load_resource(res, is_binary=True):
    """load data from features/resources"""
    path = Path(__file__).parent.parent / "features" / "resources" / res
    filemode = "rb" if is_binary else "r"
    with open(path, filemode) as fin:
        data = fin.read()
    return data


def read_program_binary(path):
    return bytearray(load_resource(path))


def read_program(context, path):
    """
    Assumes that have already added `context.app_acl` so need to have previously
    called one of the steps beginning with "Given an algod v2 client..."
    """
    if path.endswith(".teal"):
        assert hasattr(
            context, "app_acl"
        ), "Cannot compile teal program into binary because no algod v2 client has been provided in the context"

        teal = load_resource(path, is_binary=False)
        resp = context.app_acl.compile(teal)
        return base64.b64decode(resp["result"])

    return read_program_binary(path)


@given("mock server recording request paths")
def setup_mockserver(context):
    context.url = "http://127.0.0.1:" + str(context.path_server_port)
    context.acl = algod.AlgodClient("algod_token", context.url)
    context.icl = indexer.IndexerClient("indexer_token", context.url)


@given('mock http responses in "{jsonfiles}" loaded from "{directory}"')
def mock_response(context, jsonfiles, directory):
    context.url = "http://127.0.0.1:" + str(context.response_server_port)
    context.acl = algod.AlgodClient("algod_token", context.url)
    context.icl = indexer.IndexerClient("indexer_token", context.url)

    # The mock server writes this response to a file, on a regular request
    # that file is read.
    # It's an interesting approach, but currently doesn't support setting
    # the content type, or different return codes. This will require a bit
    # of extra work when/if we support the different error cases.
    #
    # Take a look at 'environment.py' to see the mock servers.
    req = Request(
        context.url + "/mock/" + directory + "/" + jsonfiles, method="GET"
    )
    urlopen(req)


@given(
    'mock http responses in "{filename}" loaded from "{directory}" with status {status}.'
)
def mock_http_responses(context, filename, directory, status):
    context.expected_status_code = int(status)
    with open("tests/features/resources/mock_response_status", "w") as f:
        f.write(status)
    mock_response(context, filename, directory)
    f = open("tests/features/resources/mock_response_path", "r")
    mock_response_path = f.read()
    f.close()
    f = open("tests/features/resources/" + mock_response_path, "r")
    expected_mock_response = f.read()
    f.close()
    expected_mock_response = bytes(expected_mock_response, "ascii")
    context.expected_mock_response = json.loads(expected_mock_response)


@when('we make any "{client}" call to "{endpoint}".')
def client_call(context, client, endpoint):
    # with the current implementation of mock responses, there is no need to do an 'endpoint' lookup
    if client == "indexer":
        try:
            context.response = context.icl.health()
        except error.IndexerHTTPError as err:
            validate_error(context, err)
    elif client == "algod":
        try:
            context.response = context.acl.status()
        except error.AlgodHTTPError as err:
            validate_error(context, err)
    else:
        raise NotImplementedError('did not recognize client "' + client + '"')


@then("the parsed response should equal the mock response.")
def parsed_equal_mock(context):
    if context.expected_status_code == 200:
        assert context.expected_mock_response == context.response


@when(
    'we make a Pending Transaction Information against txid "{txid}" with format "{response_format}"'
)
def pending_txn_info(context, txid, response_format):
    context.response = context.acl.pending_transaction_info(
        txid, response_format=response_format
    )


@when(
    'we make a Pending Transaction Information with max {max} and format "{response_format}"'
)
def pending_txn_with_max(context, max, response_format):
    context.response = context.acl.pending_transactions(
        int(max), response_format=response_format
    )


@when("we make any Pending Transactions Information call")
def pending_txn_any(context):
    context.response = context.acl.pending_transactions(
        100, response_format="msgpack"
    )


@when("we make any Pending Transaction Information call")
def pending_txn_any2(context):
    context.response = context.acl.pending_transaction_info(
        "sdfsf", response_format="msgpack"
    )


@then(
    'the parsed Pending Transaction Information response should have sender "{sender}"'
)
def parse_pending_txn(context, sender):
    context.response = json.loads(context.response)
    assert (
        encoding.encode_address(
            base64.b64decode(context.response["txn"]["txn"]["snd"])
        )
        == sender
    )


@then(
    'the parsed Pending Transactions Information response should contain an array of len {length} and element number {idx} should have sender "{sender}"'
)
def parse_pending_txns(context, length, idx, sender):
    context.response = json.loads(context.response)
    assert len(context.response["top-transactions"]) == int(length)
    assert (
        encoding.encode_address(
            base64.b64decode(
                context.response["top-transactions"][int(idx)]["txn"]["snd"]
            )
        )
        == sender
    )


@when(
    'we make a Pending Transactions By Address call against account "{account}" and max {max} and format "{response_format}"'
)
def pending_txns_by_addr(context, account, max, response_format):
    context.response = context.acl.pending_transactions_by_address(
        account, limit=int(max), response_format=response_format
    )


@when("we make any Pending Transactions By Address call")
def pending_txns_by_addr_any(context):
    context.response = context.acl.pending_transactions_by_address(
        "PNWOET7LLOWMBMLE4KOCELCX6X3D3Q4H2Q4QJASYIEOF7YIPPQBG3YQ5YI",
        response_format="msgpack",
    )


@then(
    'the parsed Pending Transactions By Address response should contain an array of len {length} and element number {idx} should have sender "{sender}"'
)
def parse_pend_by_addr(context, length, idx, sender):
    context.response = json.loads(context.response)
    assert len(context.response["top-transactions"]) == int(length)
    assert (
        encoding.encode_address(
            base64.b64decode(
                context.response["top-transactions"][int(idx)]["txn"]["snd"]
            )
        )
        == sender
    )


@when("we make any Send Raw Transaction call")
def send_any(context):
    context.response = context.acl.send_raw_transaction("Bg==")


@then('the parsed Send Raw Transaction response should have txid "{txid}"')
def parsed_send(context, txid):
    assert context.response == txid


@when("we make any Node Status call")
def status_any(context):
    context.response = context.acl.status()


@then("the parsed Node Status response should have a last round of {roundNum}")
def parse_status(context, roundNum):
    assert context.response["last-round"] == int(roundNum)


@when("we make a Status after Block call with round {block}")
def status_after(context, block):
    context.response = context.acl.status_after_block(int(block))


@when("we make any Status After Block call")
def status_after_any(context):
    context.response = context.acl.status_after_block(3)


@then(
    "the parsed Status After Block response should have a last round of {roundNum}"
)
def parse_status_after(context, roundNum):
    assert context.response["last-round"] == int(roundNum)


@when("we make any Ledger Supply call")
def ledger_any(context):
    context.response = context.acl.ledger_supply()


@then(
    "the parsed Ledger Supply response should have totalMoney {tot} onlineMoney {online} on round {roundNum}"
)
def parse_ledger(context, tot, online, roundNum):
    assert context.response["online-money"] == int(online)
    assert context.response["total-money"] == int(tot)
    assert context.response["current_round"] == int(roundNum)


@when("we make a GetAssetByID call for assetID {asset_id}")
def asset_info(context, asset_id):
    context.response = context.acl.asset_info(int(asset_id))


@when(
    'we make a Get Block call against block number {block} with format "{response_format}"'
)
def block(context, block, response_format):
    context.response = context.acl.block_info(
        int(block), response_format=response_format
    )


@when(
    'we make a Get Block call for round {round} with format "{response_format}" and header-only "{header_only}"'
)
def block(context, round, response_format, header_only):
    bool_opt = None
    if header_only == "true":
        bool_opt = True

    context.response = context.acl.block_info(
        int(round), response_format=response_format, header_only=bool_opt
    )


@when("we make any Get Block call")
def block_any(context):
    context.response = context.acl.block_info(3, response_format="msgpack")


@then('the parsed Get Block response should have rewards pool "{pool}"')
def parse_block(context, pool):
    context.response = json.loads(context.response)
    assert context.response["block"]["rwd"] == pool


@then(
    'the parsed Get Block response should have rewards pool "{pool}" and no certificate or payset'
)
def parse_block_header(context, pool):
    context.response = json.loads(context.response)
    assert context.response["block"]["rwd"] == pool
    assert (
        "cert" not in context.response
    ), f"Key 'cert' unexpectedly found in dictionary"


@then(
    'the parsed Get Block response should have heartbeat address "{hb_address}"'
)
def parse_block_heartbeat(context, hb_address):
    context.response = json.loads(context.response)

    response_address = context.response["block"]["txns"][0]["txn"]["hb"]["a"]

    # This should cover the first example, notably if this is false will fall through to check below
    if response_address == hb_address:
        return

    # Our test environment is base 64 encoding the address when it is sent as json, we need to switch the encoding to match
    response_address = encoding.encode_address(
        base64.b64decode(response_address)
    )

    assert response_address == hb_address


@when(
    'we make a Lookup Asset Balances call against asset index {index} with limit {limit} afterAddress "{afterAddress:MaybeString}" currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan}'
)
def asset_balance(
    context,
    index,
    limit,
    afterAddress,
    currencyGreaterThan,
    currencyLessThan,
):
    context.response = context.icl.asset_balances(
        int(index),
        int(limit),
        next_page=None,
        min_balance=int(currencyGreaterThan),
        max_balance=int(currencyLessThan),
    )


@when("we make any LookupAssetBalances call")
def asset_balance_any(context):
    context.response = context.icl.asset_balances(123, 10)


@when(
    'we make a Lookup Asset Transactions call against asset index {index} with NotePrefix "{notePrefixB64:MaybeString}" TxType "{txType:MaybeString}" SigType "{sigType:MaybeString}" txid "{txid:MaybeString}" round {block} minRound {minRound} maxRound {maxRound} limit {limit} beforeTime "{beforeTime:MaybeString}" afterTime "{afterTime:MaybeString}" currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} address "{address:MaybeString}" addressRole "{addressRole:MaybeString}" ExcluseCloseTo "{excludeCloseTo:MaybeString}" RekeyTo "{rekeyTo:MaybeString}"'
)
def asset_txns(
    context,
    index,
    notePrefixB64,
    txType,
    sigType,
    txid,
    block,
    minRound,
    maxRound,
    limit,
    beforeTime,
    afterTime,
    currencyGreaterThan,
    currencyLessThan,
    address,
    addressRole,
    excludeCloseTo,
    rekeyTo,
):
    if notePrefixB64 == "none":
        notePrefixB64 = ""
    if txType == "none":
        txType = None
    if sigType == "none":
        sigType = None
    if txid == "none":
        txid = None
    if beforeTime == "none":
        beforeTime = None
    if afterTime == "none":
        afterTime = None
    if address == "none":
        address = None
    if addressRole == "none":
        addressRole = None
    if excludeCloseTo == "none":
        excludeCloseTo = None
    if rekeyTo == "none":
        rekeyTo = None
    context.response = context.icl.search_asset_transactions(
        int(index),
        limit=int(limit),
        next_page=None,
        note_prefix=base64.b64decode(notePrefixB64),
        txn_type=txType,
        sig_type=sigType,
        txid=txid,
        block=int(block),
        min_round=int(minRound),
        max_round=int(maxRound),
        start_time=afterTime,
        end_time=beforeTime,
        min_amount=int(currencyGreaterThan),
        max_amount=int(currencyLessThan),
        address=address,
        address_role=addressRole,
        exclude_close_to=excludeCloseTo,
        rekey_to=rekeyTo,
    )


@when(
    'we make a Lookup Asset Transactions call against asset index {index} with NotePrefix "{notePrefixB64:MaybeString}" TxType "{txType:MaybeString}" SigType "{sigType:MaybeString}" txid "{txid:MaybeString}" round {block} minRound {minRound} maxRound {maxRound} limit {limit} beforeTime "{beforeTime:MaybeString}" afterTime "{afterTime:MaybeString}" currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} address "{address:MaybeString}" addressRole "{addressRole:MaybeString}" ExcluseCloseTo "{excludeCloseTo:MaybeString}"'
)
def asset_txns2(
    context,
    index,
    notePrefixB64,
    txType,
    sigType,
    txid,
    block,
    minRound,
    maxRound,
    limit,
    beforeTime,
    afterTime,
    currencyGreaterThan,
    currencyLessThan,
    address,
    addressRole,
    excludeCloseTo,
):
    if notePrefixB64 == "none":
        notePrefixB64 = ""
    if txType == "none":
        txType = None
    if sigType == "none":
        sigType = None
    if txid == "none":
        txid = None
    if beforeTime == "none":
        beforeTime = None
    if afterTime == "none":
        afterTime = None
    if address == "none":
        address = None
    if addressRole == "none":
        addressRole = None
    if excludeCloseTo == "none":
        excludeCloseTo = None

    context.response = context.icl.search_asset_transactions(
        int(index),
        limit=int(limit),
        next_page=None,
        note_prefix=base64.b64decode(notePrefixB64),
        txn_type=txType,
        sig_type=sigType,
        txid=txid,
        block=int(block),
        min_round=int(minRound),
        max_round=int(maxRound),
        start_time=afterTime,
        end_time=beforeTime,
        min_amount=int(currencyGreaterThan),
        max_amount=int(currencyLessThan),
        address=address,
        address_role=addressRole,
        exclude_close_to=excludeCloseTo,
        rekey_to=None,
    )


@when("we make any LookupAssetTransactions call")
def asset_txns_any(context):
    context.response = context.icl.search_asset_transactions(32)


@then(
    'the parsed LookupAssetTransactions response should be valid on round {roundNum}, and contain an array of len {length} and element number {idx} should have sender "{sender}"'
)
def parse_asset_tns(context, roundNum, length, idx, sender):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["transactions"]) == int(length)
    assert context.response["transactions"][int(idx)]["sender"] == sender


@when(
    'we make a Lookup Account Transactions call against account "{account:MaybeString}" with NotePrefix "{notePrefixB64:MaybeString}" TxType "{txType:MaybeString}" SigType "{sigType:MaybeString}" txid "{txid:MaybeString}" round {block} minRound {minRound} maxRound {maxRound} limit {limit} beforeTime "{beforeTime:MaybeString}" afterTime "{afterTime:MaybeString}" currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} assetIndex {index} rekeyTo "{rekeyTo:MaybeString}"'
)
def txns_by_addr(
    context,
    account,
    notePrefixB64,
    txType,
    sigType,
    txid,
    block,
    minRound,
    maxRound,
    limit,
    beforeTime,
    afterTime,
    currencyGreaterThan,
    currencyLessThan,
    index,
    rekeyTo,
):
    if notePrefixB64 == "none":
        notePrefixB64 = ""
    if txType == "none":
        txType = None
    if sigType == "none":
        sigType = None
    if txid == "none":
        txid = None
    if beforeTime == "none":
        beforeTime = None
    if afterTime == "none":
        afterTime = None
    if rekeyTo == "none":
        rekeyTo = None
    context.response = context.icl.search_transactions_by_address(
        asset_id=int(index),
        limit=int(limit),
        next_page=None,
        note_prefix=base64.b64decode(notePrefixB64),
        txn_type=txType,
        sig_type=sigType,
        txid=txid,
        block=int(block),
        min_round=int(minRound),
        max_round=int(maxRound),
        start_time=afterTime,
        end_time=beforeTime,
        min_amount=int(currencyGreaterThan),
        max_amount=int(currencyLessThan),
        address=account,
        rekey_to=rekeyTo,
    )


@when(
    'we make a Lookup Account Transactions call against account "{account:MaybeString}" with NotePrefix "{notePrefixB64:MaybeString}" TxType "{txType:MaybeString}" SigType "{sigType:MaybeString}" txid "{txid:MaybeString}" round {block} minRound {minRound} maxRound {maxRound} limit {limit} beforeTime "{beforeTime:MaybeString}" afterTime "{afterTime:MaybeString}" currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} assetIndex {index}'
)
def txns_by_addr2(
    context,
    account,
    notePrefixB64,
    txType,
    sigType,
    txid,
    block,
    minRound,
    maxRound,
    limit,
    beforeTime,
    afterTime,
    currencyGreaterThan,
    currencyLessThan,
    index,
):
    if notePrefixB64 == "none":
        notePrefixB64 = ""
    if txType == "none":
        txType = None
    if sigType == "none":
        sigType = None
    if txid == "none":
        txid = None
    if beforeTime == "none":
        beforeTime = None
    if afterTime == "none":
        afterTime = None
    context.response = context.icl.search_transactions_by_address(
        asset_id=int(index),
        limit=int(limit),
        next_page=None,
        note_prefix=base64.b64decode(notePrefixB64),
        txn_type=txType,
        sig_type=sigType,
        txid=txid,
        block=int(block),
        min_round=int(minRound),
        max_round=int(maxRound),
        start_time=afterTime,
        end_time=beforeTime,
        min_amount=int(currencyGreaterThan),
        max_amount=int(currencyLessThan),
        address=account,
        rekey_to=None,
    )


@when("we make any LookupAccountTransactions call")
def txns_by_addr_any(context):
    context.response = context.icl.search_transactions_by_address(
        "PNWOET7LLOWMBMLE4KOCELCX6X3D3Q4H2Q4QJASYIEOF7YIPPQBG3YQ5YI"
    )


@then(
    'the parsed LookupAccountTransactions response should be valid on round {roundNum}, and contain an array of len {length} and element number {idx} should have sender "{sender}"'
)
def parse_txns_by_addr(context, roundNum, length, idx, sender):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["transactions"]) == int(length)
    if int(length) > 0:
        assert context.response["transactions"][int(idx)]["sender"] == sender


@when(
    'we make a Lookup Block call against round {block:d} and header "{headerOnly:MaybeBool}"'
)
def lookup_block(context, block, headerOnly):
    context.response = context.icl.block_info(
        block=block, header_only=headerOnly
    )


@when("we make a Lookup Block call against round {block:d}")
def lookup_block(context, block):
    context.response = context.icl.block_info(block)


@when("we make any LookupBlock call")
def lookup_block_any(context):
    context.response = context.icl.block_info(12)


@then(
    'the parsed LookupBlock response should have previous block hash "{prevHash}"'
)
def parse_lookup_block(context, prevHash):
    assert context.response["previous-block-hash"] == prevHash


def parse_args(assetid):
    t = assetid.split(" ")
    l = {
        "assetid": t[2],
        "currencygt": t[4][:-1],
        "currencylt": t[5][:-1],
        "limit": t[6],
        "token": t[9][1:-1],
    }
    return l


@when("we make a Lookup Asset by ID call against asset index {index}")
def lookup_asset(context, index):
    context.response = context.icl.asset_info(int(index))


@when("we make any LookupAssetByID call")
def lookup_asset_any(context):
    context.response = context.icl.asset_info(1)


@then("the parsed LookupAssetByID response should have index {index}")
def parse_asset(context, index):
    assert context.response["asset"]["index"] == int(index)


@when(
    'we make a Search For Transactions call with account "{account:MaybeString}" NotePrefix "{notePrefixB64:MaybeString}" TxType "{txType:MaybeString}" SigType "{sigType:MaybeString}" txid "{txid:MaybeString}" round {block} minRound {minRound} maxRound {maxRound} limit {limit} beforeTime "{beforeTime:MaybeString}" afterTime "{afterTime:MaybeString}" currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} assetIndex {index} addressRole "{addressRole:MaybeString}" ExcluseCloseTo "{excludeCloseTo:MaybeString}" groupid "{groupidB64:MaybeString}" rekeyTo "{rekeyTo:MaybeString}"'
)
def search_txns(
    context,
    account,
    notePrefixB64,
    txType,
    sigType,
    txid,
    block,
    minRound,
    maxRound,
    limit,
    beforeTime,
    afterTime,
    currencyGreaterThan,
    currencyLessThan,
    index,
    addressRole,
    excludeCloseTo,
    groupidB64,
    rekeyTo,
):
    if notePrefixB64 == "none":
        notePrefixB64 = ""
    if txType == "none":
        txType = None
    if sigType == "none":
        sigType = None
    if txid == "none":
        txid = None
    if beforeTime == "none":
        beforeTime = None
    if afterTime == "none":
        afterTime = None
    if account == "none":
        account = None
    if addressRole == "none":
        addressRole = None
    if excludeCloseTo == "none":
        excludeCloseTo = None
    if groupidB64 == "none":
        groupidB64 = ""
    if rekeyTo == "none":
        rekeyTo = None
    context.response = context.icl.search_transactions(
        asset_id=int(index),
        limit=int(limit),
        next_page=None,
        note_prefix=base64.b64decode(notePrefixB64),
        txn_type=txType,
        sig_type=sigType,
        txid=txid,
        block=int(block),
        min_round=int(minRound),
        max_round=int(maxRound),
        start_time=afterTime,
        end_time=beforeTime,
        min_amount=int(currencyGreaterThan),
        max_amount=int(currencyLessThan),
        address=account,
        address_role=addressRole,
        exclude_close_to=excludeCloseTo,
        group_id=base64.b64decode(groupidB64),
        rekey_to=rekeyTo,
    )


@when(
    'we make a Search For Transactions call with account "{account:MaybeString}" NotePrefix "{notePrefixB64:MaybeString}" TxType "{txType:MaybeString}" SigType "{sigType:MaybeString}" txid "{txid:MaybeString}" round {block} minRound {minRound} maxRound {maxRound} limit {limit} beforeTime "{beforeTime:MaybeString}" afterTime "{afterTime:MaybeString}" currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} assetIndex {index} addressRole "{addressRole:MaybeString}" ExcluseCloseTo "{excludeCloseTo:MaybeString}" groupid "{groupidB64:MaybeString}"'
)
def search_txns2(
    context,
    account,
    notePrefixB64,
    txType,
    sigType,
    txid,
    block,
    minRound,
    maxRound,
    limit,
    beforeTime,
    afterTime,
    currencyGreaterThan,
    currencyLessThan,
    index,
    addressRole,
    excludeCloseTo,
    groupidB64,
):
    if notePrefixB64 == "none":
        notePrefixB64 = ""
    if txType == "none":
        txType = None
    if sigType == "none":
        sigType = None
    if txid == "none":
        txid = None
    if beforeTime == "none":
        beforeTime = None
    if afterTime == "none":
        afterTime = None
    if account == "none":
        account = None
    if addressRole == "none":
        addressRole = None
    if excludeCloseTo == "none":
        excludeCloseTo = None
    if groupidB64 == "none":
        groupidB64 = ""
    context.response = context.icl.search_transactions(
        asset_id=int(index),
        limit=int(limit),
        next_page=None,
        note_prefix=base64.b64decode(notePrefixB64),
        txn_type=txType,
        sig_type=sigType,
        txid=txid,
        block=int(block),
        min_round=int(minRound),
        max_round=int(maxRound),
        start_time=afterTime,
        end_time=beforeTime,
        min_amount=int(currencyGreaterThan),
        max_amount=int(currencyLessThan),
        address=account,
        address_role=addressRole,
        exclude_close_to=excludeCloseTo,
        group_id=base64.b64decode(groupidB64),
        rekey_to=None,
    )


@when(
    'we make a Search For BlockHeaders call with minRound {minRound} maxRound {maxRound} limit {limit} nextToken "{next:MaybeString}" beforeTime "{beforeTime:MaybeString}" afterTime "{afterTime:MaybeString}" proposers {proposers} expired {expired} absent {absent}'
)
def search_block_headers(
    context,
    minRound,
    maxRound,
    limit,
    next,
    beforeTime,
    afterTime,
    proposers,
    expired,
    absent,
):
    if next == "none":
        next = None
    if beforeTime == "none":
        beforeTime = None
    if afterTime == "none":
        afterTime = None
    if not proposers or proposers == '""':
        proposers = None
    else:
        proposers = eval(proposers)
    if not expired or expired == '""':
        expired = None
    else:
        expired = eval(expired)
    if not absent or absent == '""':
        absent = None
    else:
        absent = eval(absent)

    context.response = context.icl.search_block_headers(
        limit=int(limit),
        next_page=next,
        min_round=int(minRound),
        max_round=int(maxRound),
        start_time=afterTime,
        end_time=beforeTime,
        proposers=proposers,
        expired=expired,
        absent=absent,
    )


@when("we make any SearchForTransactions call")
def search_txns_any(context):
    context.response = context.icl.search_transactions(asset_id=2)


@then(
    'the parsed SearchForTransactions response should be valid on round {roundNum} and the array should be of len {length} and the element at index {index} should have sender "{sender}"'
)
def parse_search_txns(context, roundNum, length, index, sender):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["transactions"]) == int(length)
    if int(length) > 0:
        assert context.response["transactions"][int(index)]["sender"] == sender


@when(
    'the parsed SearchForTransactions response should be valid on round {roundNum} and the array should be of len {length} and the element at index {index} should have rekey-to "{rekeyTo:MaybeString}"'
)
def parsed_search_for_txns(context, roundNum, length, index, rekeyTo):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["transactions"]) == int(length)
    if int(length) > 0:
        assert (
            context.response["transactions"][int(index)]["rekey-to"] == rekeyTo
        )


@then(
    'the parsed SearchForTransactions response should be valid on round {roundNum} and the array should be of len {length} and the element at index {index} should have hbaddress "{hb_address}"'
)
def parsed_search_for_hb_txns(context, roundNum, length, index, hb_address):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["transactions"]) == int(length)
    if int(length) > 0:
        assert (
            context.response["transactions"][int(index)][
                "heartbeat-transaction"
            ]["hb-address"]
            == hb_address
        )


@when("we make any SearchForBlockHeaders call")
def search_bhs_any(context):
    context.response = context.icl.search_block_headers()


@then(
    'the parsed SearchForBlockHeaders response should have a block array of len {length} and the element at index {index} should have round "{round}"'
)
def step_impl(context, length, index, round):
    assert len(context.response["blocks"]) == int(length)
    assert (context.response["blocks"][int(index)]["round"]) == int(round)


@when(
    'we make a SearchForAssets call with limit {limit} creator "{creator:MaybeString}" name "{name:MaybeString}" unit "{unit:MaybeString}" index {index}'
)
def search_assets(context, limit, creator, name, unit, index):
    if creator == "none":
        creator = None
    if name == "none":
        name = None
    if unit == "none":
        unit = None

    context.response = context.icl.search_assets(
        limit=int(limit),
        next_page=None,
        creator=creator,
        name=name,
        unit=unit,
        asset_id=int(index),
    )


@when("we make any SearchForAssets call")
def search_assets_any(context):
    context.response = context.icl.search_assets(asset_id=2)


@then(
    "the parsed SearchForAssets response should be valid on round {roundNum} and the array should be of len {length} and the element at index {index} should have asset index {assetIndex}"
)
def parse_search_assets(context, roundNum, length, index, assetIndex):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["assets"]) == int(length)
    if int(length) > 0:
        assert context.response["assets"][int(index)]["index"] == int(
            assetIndex
        )


@when("we make any Suggested Transaction Parameters call")
def suggested_any(context):
    context.response = context.acl.suggested_params()


@then(
    "the parsed Suggested Transaction Parameters response should have first round valid of {roundNum}"
)
def parse_suggested(context, roundNum):
    assert context.response.first == int(roundNum)


@then('expect the path used to be "{path}"')
def expect_path(context, path):
    if not isinstance(context.response, dict):
        try:
            context.response = json.loads(context.response)
        except json.JSONDecodeError:
            pass
    exp_path, exp_query = urllib.parse.splitquery(path)
    exp_query = urllib.parse.parse_qs(exp_query)

    actual_path, actual_query = urllib.parse.splitquery(
        context.response["path"]
    )
    actual_query = urllib.parse.parse_qs(actual_query)
    actual_path = actual_path.replace("%3A", ":")
    assert exp_path == actual_path, f"{exp_path} != {actual_path}"
    assert exp_query == actual_query, f"{exp_query} != {actual_query}"


@then('expect the request to be "{method}" "{path}"')
def expect_request(context, method, path):
    return expect_path(context, path)


@then('expect error string to contain "{err:MaybeString}"')
def expect_error(context, err):
    # TODO: this should actually do the claimed action
    pass


@given(
    'suggested transaction parameters fee {fee}, flat-fee "{flat_fee:MaybeBool}", first-valid {first_valid}, last-valid {last_valid}, genesis-hash "{genesis_hash}", genesis-id "{genesis_id}"'
)
def suggested_transaction_parameters(
    context, fee, flat_fee, first_valid, last_valid, genesis_hash, genesis_id
):
    context.suggested_params = transaction.SuggestedParams(
        fee=int(fee),
        flat_fee=flat_fee,
        first=int(first_valid),
        last=int(last_valid),
        gh=genesis_hash,
        gen=genesis_id,
    )


@when(
    'I build a keyreg transaction with sender "{sender}", nonparticipation "{nonpart:MaybeBool}", vote first {vote_first}, vote last {vote_last}, key dilution {key_dilution}, vote public key "{vote_pk:MaybeString}", selection public key "{selection_pk:MaybeString}", and state proof public key "{state_proof_pk:MaybeString}"'
)
def build_keyreg_txn(
    context,
    sender,
    nonpart,
    vote_first,
    vote_last,
    key_dilution,
    vote_pk,
    selection_pk,
    state_proof_pk,
):
    if nonpart:
        context.transaction = transaction.KeyregNonparticipatingTxn(
            sender, context.suggested_params
        )
        return

    if len(vote_pk) == 0:
        vote_pk = None
    if len(selection_pk) == 0:
        selection_pk = None
    if len(state_proof_pk) == 0:
        state_proof_pk = None

    if vote_pk is None and selection_pk is None and state_proof_pk is None:
        context.transaction = transaction.KeyregOfflineTxn(
            sender, context.suggested_params
        )
        return

    context.transaction = transaction.KeyregOnlineTxn(
        sender,
        context.suggested_params,
        vote_pk,
        selection_pk,
        int(vote_first),
        int(vote_last),
        int(key_dilution),
        sprfkey=state_proof_pk,
    )


@given("suggested transaction parameters from the algod v2 client")
def get_sp_from_algod(context):
    context.suggested_params = context.app_acl.suggested_params()


@step(
    'I build a payment transaction with sender "{sender:MaybeString}", receiver "{receiver:MaybeString}", amount {amount}, close remainder to "{close_remainder_to:MaybeString}"'
)
def build_payment_transaction(
    context, sender, receiver, amount, close_remainder_to
):
    if sender == "transient":
        sender = context.transient_pk
    if receiver == "transient":
        receiver = context.transient_pk
    if not close_remainder_to:
        close_remainder_to = None
    context.transaction = transaction.PaymentTxn(
        sender=sender,
        sp=context.suggested_params,
        receiver=receiver,
        amt=int(amount),
        close_remainder_to=close_remainder_to,
    )


@when("sign the transaction")
def sign_transaction_with_signing_account(context):
    private_key = mnemonic.to_private_key(context.signing_mnemonic)
    context.signed_transaction = context.transaction.sign(private_key)


@then('the base64 encoded signed transactions should equal "{goldens}"')
def compare_stxns_array_to_base64_golden(context, goldens):
    golden_strings = goldens.split(",")
    assert len(golden_strings) == len(context.signed_transactions)
    for i, golden in enumerate(golden_strings):
        actual_base64 = encoding.msgpack_encode(context.signed_transactions[i])
        assert golden == actual_base64, "actual is {}".format(actual_base64)


@then('the base64 encoded signed transaction should equal "{golden}"')
def compare_to_base64_golden(context, golden):
    actual_base64 = encoding.msgpack_encode(context.signed_transaction)
    assert golden == actual_base64, "actual is {}".format(actual_base64)


@then("the decoded transaction should equal the original")
def compare_to_original(context):
    encoded = encoding.msgpack_encode(context.signed_transaction)
    decoded = encoding.msgpack_decode(encoded)
    assert decoded.transaction == context.transaction


@given(
    'an algod v2 client connected to "{host}" port {port} with token "{token}"'
)
def algod_v2_client_at_host_port_and_token(context, host, port, token):
    algod_address = "http://" + str(host) + ":" + str(port)
    context.app_acl = algod.AlgodClient(token, algod_address)


@given("an algod v2 client")
def algod_v2_client(context):
    algod_address = "http://localhost" + ":" + str(algod_port)
    context.app_acl = algod.AlgodClient(daemon_token, algod_address)


@given("an indexer v2 client")
def indexer_v2_client(context):
    indexer_address = "http://localhost" + ":" + str(indexer_port)
    context.app_icl = indexer.IndexerClient("", indexer_address)


@when('I compile a teal program "{program}"')
def compile_step(context, program):
    data = load_resource(program)
    source = data.decode("utf-8")

    try:
        context.response = context.app_acl.compile(source)
        context.status = 200
    except AlgodHTTPError as ex:
        context.status = ex.code
        context.response = dict(result="", hash="")


@then(
    'it is compiled with {status} and "{result:MaybeString}" and "{hash:MaybeString}"'
)
def compile_check_step(context, status, result, hash):
    assert context.status == int(status)
    assert context.response["result"] == result
    assert context.response["hash"] == hash


@then(
    'base64 decoding the response is the same as the binary "{binary:MaybeString}"'
)
def b64decode_compiled_teal_step(context, binary):
    binary = load_resource(binary)
    response_result = context.response["result"]
    assert base64.b64decode(response_result.encode()) == binary


@then('disassembly of "{bytecode_filename}" matches "{source_filename}"')
def disassembly_matches_source(context, bytecode_filename, source_filename):
    bytecode = load_resource(bytecode_filename)
    expected_source = load_resource(source_filename).decode("utf-8")

    context.response = context.app_acl.disassemble(bytecode)
    actual_source = context.response["result"]

    assert actual_source == expected_source


@when('I dryrun a "{kind}" program "{program}"')
def dryrun_step(context, kind, program):
    data = load_resource(program)
    sp = transaction.SuggestedParams(
        int(1000), int(1), int(100), "", flat_fee=True
    )
    zero_addr = encoding.encode_address(bytes(32))
    txn = transaction.Transaction(zero_addr, sp, None, None, "pay", None)
    sources = []

    if kind == "compiled":
        lsig = transaction.LogicSigAccount(bytes(data))
        txns = [transaction.LogicSigTransaction(txn, lsig)]
    elif kind == "source":
        txns = [transaction.SignedTransaction(txn, None)]
        sources = [DryrunSource(field_name="lsig", source=data, txn_index=0)]
    else:
        assert False, f"kind {kind} not in (source, compiled)"

    drr = DryrunRequest(txns=txns, sources=sources)
    context.response = context.app_acl.dryrun(drr)


@then('I get execution result "{result}"')
def dryrun_check_step(context, result):
    ddr = context.response
    assert len(ddr["txns"]) > 0

    res = ddr["txns"][0]
    if (
        res["logic-sig-messages"] is not None
        and len(res["logic-sig-messages"]) > 0
    ):
        msgs = res["logic-sig-messages"]
    elif (
        res["app-call-messages"] is not None
        and len(res["app-call-messages"]) > 0
    ):
        msgs = res["app-call-messages"]

    assert len(msgs) > 0
    assert msgs[-1] == result


@when("we make any Dryrun call")
def dryrun_any_call_step(context):
    context.response = context.acl.dryrun(DryrunRequest())


@then(
    'the parsed Dryrun Response should have global delta "{creator}" with {action}'
)
def dryrun_parsed_response(context, creator, action):
    ddr = context.response
    assert len(ddr["txns"]) > 0

    delta = ddr["txns"][0]["global-delta"]
    assert len(delta) > 0
    assert delta[0]["key"] == creator
    assert delta[0]["value"]["action"] == int(action)


@given('dryrun test case with "{program}" of type "{kind}"')
def dryrun_test_case_step(context, program, kind):
    if kind not in set(["lsig", "approv", "clearp"]):
        assert False, f"kind {kind} not in (lsig, approv, clearp)"

    prog = load_resource(program)
    # check if source
    if prog[0] > 0x20:
        prog = prog.decode("utf-8")

    context.dryrun_case_program = prog
    context.dryrun_case_kind = kind


@then('status assert of "{status}" is succeed')
def dryrun_test_case_status_assert_step(context, status):
    class TestCase(DryrunTestCaseMixin, unittest.TestCase):
        """Mock TestCase to test"""

    ts = TestCase()
    ts.algo_client = context.app_acl

    lsig = None
    app = None
    if context.dryrun_case_kind == "lsig":
        lsig = dict()
    if context.dryrun_case_kind == "approv":
        app = dict()
    elif context.dryrun_case_kind == "clearp":
        app = dict(on_complete=transaction.OnComplete.ClearStateOC)

    if status == "PASS":
        ts.assertPass(context.dryrun_case_program, lsig=lsig, app=app)
    else:
        ts.assertReject(context.dryrun_case_program, lsig=lsig, app=app)


def dryrun_test_case_global_state_assert_impl(
    context, key, value, action, raises
):
    class TestCase(DryrunTestCaseMixin, unittest.TestCase):
        """Mock TestCase to test"""

    ts = TestCase()
    ts.algo_client = context.app_acl

    action = int(action)

    val = dict(action=action)
    if action == 1:
        val["bytes"] = value
    elif action == 2:
        val["uint"] = int(value)

    on_complete = transaction.OnComplete.NoOpOC
    if context.dryrun_case_kind == "clearp":
        on_complete = transaction.OnComplete.ClearStateOC

    raised = False
    try:
        ts.assertGlobalStateContains(
            context.dryrun_case_program,
            dict(key=key, value=val),
            app=dict(on_complete=on_complete),
        )
    except AssertionError:
        raised = True

    if raises:
        ts.assertTrue(raised, "assertGlobalStateContains expected to raise")


@then('global delta assert with "{key}", "{value}" and {action} is succeed')
def dryrun_test_case_global_state_assert_step(context, key, value, action):
    dryrun_test_case_global_state_assert_impl(
        context, key, value, action, False
    )


@then('global delta assert with "{key}", "{value}" and {action} is failed')
def dryrun_test_case_global_state_assert_fail_step(
    context, key, value, action
):
    dryrun_test_case_global_state_assert_impl(
        context, key, value, action, True
    )


@then(
    'local delta assert for "{account}" of accounts {index} with "{key}", "{value}" and {action} is succeed'
)
def dryrun_test_case_local_state_assert_fail_step(
    context, account, index, key, value, action
):
    class TestCase(DryrunTestCaseMixin, unittest.TestCase):
        """Mock TestCase to test"""

    ts = TestCase()
    ts.algo_client = context.app_acl

    action = int(action)

    val = dict(action=action)
    if action == 1:
        val["bytes"] = value
    elif action == 2:
        val["uint"] = int(value)

    on_complete = transaction.OnComplete.NoOpOC
    if context.dryrun_case_kind == "clearp":
        on_complete = transaction.OnComplete.ClearStateOC

    app_idx = 1
    accounts = [
        Account(
            address=ts.default_address(),
            status="Offline",
            apps_local_state=[ApplicationLocalState(id=app_idx)],
        )
    ] * 2
    accounts[int(index)].address = account

    drr = ts.dryrun_request(
        context.dryrun_case_program,
        sender=accounts[0].address,
        app=dict(app_idx=app_idx, on_complete=on_complete, accounts=accounts),
    )

    ts.assertNoError(drr)
    ts.assertLocalStateContains(drr, account, dict(key=key, value=val))


@then(
    'the produced json should equal "{json_path}" loaded from "{json_directory}"'
)
def check_json_output_equals(context, json_path, json_directory):
    with open(
        "tests/features/unit/" + json_directory + "/" + json_path, "rb"
    ) as f:
        loaded_response = json.load(f)
    assert context.json_output == loaded_response


@given(
    'a dryrun response file "{dryrun_response_file}" and a transaction at index "{txn_id}"'
)
def parse_dryrun_response_object(context, dryrun_response_file, txn_id):
    dir_path = os.path.dirname(os.path.realpath(__file__))
    dir_path = os.path.dirname(os.path.dirname(dir_path))
    with open(
        dir_path + "/tests/features/resources/" + dryrun_response_file, "r"
    ) as f:
        drr_dict = json.loads(f.read())

    context.dryrun_response_object = dryrun_results.DryrunResponse(drr_dict)
    context.dryrun_txn_result = context.dryrun_response_object.txns[
        int(txn_id)
    ]


@then('calling app trace produces "{app_trace_file}"')
def dryrun_compare_golden(context, app_trace_file):
    trace_expected = load_resource(app_trace_file, is_binary=False)

    dryrun_trace = context.dryrun_txn_result.app_trace()

    got_lines = dryrun_trace.split("\n")
    expected_lines = trace_expected.split("\n")

    print("{} {}".format(len(got_lines), len(expected_lines)))
    for idx in range(len(got_lines)):
        if got_lines[idx] != expected_lines[idx]:
            print(
                "  {}  \n{}\n{}\n".format(
                    idx, got_lines[idx], expected_lines[idx]
                )
            )

    assert trace_expected == dryrun_trace, "Expected \n{}\ngot\n{}\n".format(
        trace_expected, dryrun_trace
    )


@then(
    'I dig into the paths "{paths}" of the resulting atomic transaction tree I see group ids and they are all the same'
)
def same_groupids_for_paths(context, paths):
    paths = [[int(p) for p in path.split(",")] for path in paths.split(":")]
    grp = None
    for path in paths:
        d = context.atomic_transaction_composer_return.abi_results
        for idx, p in enumerate(path):
            d = d["inner-txns"][p] if idx else d[idx].tx_info
            _grp = d["txn"]["txn"]["grp"]
        if not grp:
            grp = _grp
        else:
            assert grp == _grp, f"non-constant txn group hashes {_grp} v {grp}"


@then(
    'I can dig the {i}th atomic result with path "{path}" and see the value "{field}"'
)
def glom_app_eval_delta(context, i, path, field):
    results = context.atomic_transaction_composer_return.abi_results
    actual_field = glom(results[int(i)].tx_info, path)
    assert field == str(
        actual_field
    ), f"path [{path}] expected value [{field}] but got [{actual_field}] instead"


@given('a source map json file "{sourcemap_file}"')
def parse_source_map(context, sourcemap_file):
    jsmap = json.loads(load_resource(sourcemap_file, is_binary=False))
    context.source_map = source_map.SourceMap(jsmap)


@then('the string composed of pc:line number equals "{pc_to_line}"')
def check_source_map(context, pc_to_line):
    buff = [
        f"{pc}:{line}" for pc, line in context.source_map.pc_to_line.items()
    ]
    actual = ";".join(buff)
    assert actual == pc_to_line, f"expected {pc_to_line} got {actual}"


@then('getting the line associated with a pc "{pc}" equals "{line}"')
def check_pc_to_line(context, pc, line):
    actual_line = context.source_map.get_line_for_pc(int(pc))
    assert actual_line == int(line), f"expected line {line} got {actual_line}"


@then('getting the last pc associated with a line "{line}" equals "{pc}"')
def check_line_to_pc(context, line, pc):
    actual_pcs = context.source_map.get_pcs_for_line(int(line))
    assert actual_pcs[-1] == int(pc), f"expected pc {pc} got {actual_pcs[-1]}"


@when('I compile a teal program "{teal}" with mapping enabled')
def check_compile_mapping(context, teal):
    data = load_resource(teal)
    source = data.decode("utf-8")
    response = context.app_acl.compile(source, source_map=True)
    context.raw_source_map = json.dumps(
        response["sourcemap"], separators=(",", ":")
    )


@then('the resulting source map is the same as the json "{sourcemap}"')
def check_mapping_equal(context, sourcemap):
    expected = load_resource(sourcemap).decode("utf-8").strip()
    nl = "\n"
    assert (
        context.raw_source_map == expected
    ), f"context.raw_source_map={nl}{context.raw_source_map}{nl}expected={nl}{expected}"


@when("we make a GetLightBlockHeaderProof call for round {round}")
def lightblock(context, round):
    context.response = context.acl.lightblockheader_proof(round)


@when("we make a GetStateProof call for round {round}")
def state_proofs(context, round):
    context.response = context.acl.stateproofs(round)


@when(
    'we make a GetTransactionProof call for round {round} txid "{txid}" and hashtype "{hashtype:MaybeString}"'
)
def transaction_proof(context, round, txid, hashtype):
    context.response = context.acl.transaction_proof(
        round, txid, hashtype, "msgpack"
    )


@when("we make a Lookup Block Hash call against round {round}")
def get_block_hash(context, round):
    context.response = context.acl.get_block_hash(round)


@when("I simulate the transaction")
def simulate_transaction(context):
    context.simulate_response = context.app_acl.simulate_raw_transactions(
        [context.stx]
    )


@then("the simulation should succeed without any failure message")
def simulate_transaction_succeed(context):
    resp = (
        context.simulate_response
        if hasattr(context, "simulate_response")
        else context.atomic_transaction_composer_return.simulate_response
    )

    for group in resp["txn-groups"]:
        assert "failure-message" not in group


@then("I simulate the current transaction group with the composer")
def simulate_atc(context):
    context.atomic_transaction_composer_return = (
        context.atomic_transaction_composer.simulate(context.app_acl)
    )


@then(
    'the simulation should report a failure at group "{group}", path "{path}" with message "{message}"'
)
def simulate_atc_failure(context, group, path, message):
    if hasattr(context, "simulate_response"):
        resp = context.simulate_response
    else:
        resp = context.atomic_transaction_composer_return.simulate_response
    group_idx: int = int(group)
    fail_path = ",".join(
        [str(pe) for pe in resp["txn-groups"][group_idx]["failed-at"]]
    )
    assert fail_path == path
    assert message in resp["txn-groups"][group_idx]["failure-message"]


@when("I make a new simulate request.")
def make_simulate_request(context):
    context.simulate_request = SimulateRequest(txn_groups=[])


@then("I allow more logs on that simulate request.")
def allow_more_logs_in_request(context):
    context.simulate_request.allow_more_logs = True


@then("I simulate the transaction group with the simulate request.")
def simulate_group_with_request(context):
    context.atomic_transaction_composer_return = (
        context.atomic_transaction_composer.simulate(
            context.app_acl, context.simulate_request
        )
    )


@then("I check the simulation result has power packs allow-more-logging.")
def power_pack_simulation_should_have_more_logging(context):
    assert context.atomic_transaction_composer_return.eval_overrides
    assert (
        context.atomic_transaction_composer_return.eval_overrides.max_log_calls
    )
    assert (
        context.atomic_transaction_composer_return.eval_overrides.max_log_size
    )


@when("I prepare the transaction without signatures for simulation")
def prepare_txn_without_signatures(context):
    context.stx = transaction.SignedTransaction(context.txn, None)


@then("I allow {budget} more budget on that simulate request.")
def allow_more_budget_simulation(context, budget):
    context.simulate_request.extra_opcode_budget = int(budget)


@then(
    "I check the simulation result has power packs extra-opcode-budget with extra budget {budget}."
)
def power_pack_simulation_should_have_extra_budget(context, budget):
    assert context.atomic_transaction_composer_return.eval_overrides
    assert (
        context.atomic_transaction_composer_return.eval_overrides.extra_opcode_budget
        == int(budget)
    )


@then(
    'I allow exec trace options "{options:MaybeString}" on that simulate request.'
)
def exec_trace_config_in_simulation(context, options: str):
    option_list = options.split(",")
    context.simulate_request.exec_trace_config = SimulateTraceConfig(
        enable=True,
        stack_change="stack" in option_list,
        scratch_change="scratch" in option_list,
        state_change="state" in option_list,
    )


def compare_avm_value_with_string_literal(
    expected_string_literal: str, actual_avm_value: dict
):
    [expected_avm_type, expected_value] = expected_string_literal.split(":")

    if expected_avm_type == "uint64":
        assert actual_avm_value["type"] == 2
        if expected_value == "0":
            assert "uint" not in actual_avm_value
        else:
            assert actual_avm_value["uint"] == int(expected_value)
    elif expected_avm_type == "bytes":
        assert actual_avm_value["type"] == 1
        if len(expected_value) == 0:
            assert "bytes" not in actual_avm_value
        else:
            # expected_value and actual bytes should both be b64 encoded
            assert actual_avm_value["bytes"] == expected_value
    else:
        raise Exception(f"Unknown AVM type: {expected_avm_type}")


@then(
    '{unit_index}th unit in the "{trace_type}" trace at txn-groups path "{group_path}" should add value "{stack_addition:MaybeString}" to stack, pop {pop_count} values from stack, write value "{scratch_var:MaybeString}" to scratch slot "{scratch_index:MaybeString}".'
)
def exec_trace_unit_in_simulation_check_stack_scratch(
    context,
    unit_index,
    trace_type,
    group_path,
    stack_addition: str,
    pop_count,
    scratch_var,
    scratch_index,
):
    assert context.atomic_transaction_composer_return
    assert context.atomic_transaction_composer_return.simulate_response

    simulation_response = (
        context.atomic_transaction_composer_return.simulate_response
    )
    assert "txn-groups" in simulation_response

    assert simulation_response["txn-groups"]

    group_path = list(map(int, group_path.split(",")))
    traces = simulation_response["txn-groups"][0]["txn-results"][
        group_path[0]
    ]["exec-trace"]
    assert traces

    for p in group_path[1:]:
        traces = traces["inner-trace"][p]
        assert traces

    trace = []
    if trace_type == "approval":
        trace = traces["approval-program-trace"]
    elif trace_type == "clearState":
        trace = traces["clear-state-program-trace"]
    elif trace_type == "logic":
        trace = traces["logic-sig-trace"]

    assert trace

    unit_index = int(unit_index)
    unit = trace[unit_index]

    pop_count = int(pop_count)
    if pop_count > 0:
        assert unit["stack-pop-count"]
        assert unit["stack-pop-count"] == pop_count
    else:
        assert "stack-pop-count" not in unit

    stack_additions = list(
        filter(lambda x: len(x) > 0, stack_addition.split(","))
    )
    if len(stack_additions) > 0:
        for i in range(0, len(stack_additions)):
            compare_avm_value_with_string_literal(
                stack_additions[i], unit["stack-additions"][i]
            )
    else:
        assert "stack-additions" not in unit

    if len(scratch_index) > 0:
        scratch_index = int(scratch_index)
        assert unit["scratch-changes"]
        assert len(unit["scratch-changes"]) == 1
        assert unit["scratch-changes"][0]["slot"] == scratch_index
        compare_avm_value_with_string_literal(
            scratch_var, unit["scratch-changes"][0]["new-value"]
        )
    else:
        assert len(scratch_var) == 0


@then('the current application initial "{state_type}" state should be empty.')
def current_app_initial_state_should_be_empty(context, state_type):
    assert context.atomic_transaction_composer_return
    assert context.atomic_transaction_composer_return.simulate_response
    simulation_response = (
        context.atomic_transaction_composer_return.simulate_response
    )

    assert simulation_response["initial-states"]
    app_initial_states = simulation_response["initial-states"][
        "app-initial-states"
    ]
    assert app_initial_states

    initial_app_state = None
    found = False
    for app_state in app_initial_states:
        if app_state["id"] == context.current_application_id:
            initial_app_state = app_state
            found = True
            break
    assert found
    if initial_app_state:
        if state_type == "local":
            assert "app-locals" not in initial_app_state
        elif state_type == "global":
            assert "app-globals" not in initial_app_state
        elif state_type == "box":
            assert "app-boxes" not in initial_app_state
        else:
            raise Exception(f"Unknown state type: {state_type}")


@then(
    'the current application initial "{state_type}" state should contain "{key_str}" with value "{value_str}".'
)
def current_app_initial_state_should_contain_key_value(
    context, state_type, key_str, value_str
):
    assert context.atomic_transaction_composer_return
    assert context.atomic_transaction_composer_return.simulate_response
    simulation_response = (
        context.atomic_transaction_composer_return.simulate_response
    )

    assert simulation_response["initial-states"]
    app_initial_states = simulation_response["initial-states"][
        "app-initial-states"
    ]
    assert app_initial_states

    initial_app_state = None
    for app_state in app_initial_states:
        if app_state["id"] == context.current_application_id:
            initial_app_state = app_state
            break
    assert initial_app_state is not None
    kvs = None
    if state_type == "local":
        assert "app-locals" in initial_app_state
        assert isinstance(initial_app_state["app-locals"], list)
        assert len(initial_app_state["app-locals"]) == 1
        assert "account" in initial_app_state["app-locals"][0]
        # TODO: verify account is an algorand address
        assert "kvs" in initial_app_state["app-locals"][0]
        assert isinstance(initial_app_state["app-locals"][0]["kvs"], list)
        kvs = initial_app_state["app-locals"][0]["kvs"]
    elif state_type == "global":
        assert "app-globals" in initial_app_state
        assert "account" not in initial_app_state["app-globals"]
        assert "kvs" in initial_app_state["app-globals"]
        assert isinstance(initial_app_state["app-globals"]["kvs"], list)
        kvs = initial_app_state["app-globals"]["kvs"]
    elif state_type == "box":
        assert "app-boxes" in initial_app_state
        assert "account" not in initial_app_state["app-boxes"]
        assert "kvs" in initial_app_state["app-boxes"]
        assert isinstance(initial_app_state["app-boxes"]["kvs"], list)
        kvs = initial_app_state["app-boxes"]["kvs"]
    else:
        raise Exception(f"Unknown state type: {state_type}")
    assert isinstance(kvs, list)
    assert len(kvs) > 0

    actual_value = None
    b64_key = base64.b64encode(key_str.encode()).decode()
    for kv in kvs:
        assert "key" in kv
        assert "value" in kv
        if kv["key"] == b64_key:
            actual_value = kv["value"]
            break
    assert actual_value is not None
    compare_avm_value_with_string_literal(value_str, actual_value)


@then(
    '{unit_index}th unit in the "{trace_type}" trace at txn-groups path "{txn_group_path}" should write to "{state_type}" state "{state_key}" with new value "{state_new_value}".'
)
def trace_unit_should_write_to_state_with_value(
    context,
    unit_index,
    trace_type,
    txn_group_path,
    state_type,
    state_key,
    state_new_value,
):
    def unit_finder(
        simulation_response: dict,
        txn_group_path: str,
        trace_type: str,
        unit_index: int,
    ) -> dict:
        txn_group_path_split = [
            int(p) for p in txn_group_path.split(",") if p != ""
        ]
        assert len(txn_group_path_split) > 0

        traces = simulation_response["txn-groups"][0]["txn-results"][
            txn_group_path_split[0]
        ]["exec-trace"]
        assert traces

        for p in txn_group_path_split[1:]:
            traces = traces["inner-trace"][p]
            assert traces

        trace = None
        if trace_type == "approval":
            trace = traces["approval-program-trace"]
        elif trace_type == "clearState":
            trace = traces["clear-state-program-trace"]
        elif trace_type == "logic":
            trace = traces["logic-sig-trace"]
        else:
            raise Exception(f"Unknown trace type: {trace_type}")

        assert unit_index < len(trace)
        return trace[unit_index]

    assert context.atomic_transaction_composer_return
    assert context.atomic_transaction_composer_return.simulate_response
    simulation_response = (
        context.atomic_transaction_composer_return.simulate_response
    )

    change_unit = unit_finder(
        simulation_response, txn_group_path, trace_type, int(unit_index)
    )
    assert change_unit["state-changes"]
    assert len(change_unit["state-changes"]) == 1
    state_change = change_unit["state-changes"][0]

    if state_type == "global":
        assert state_change["app-state-type"] == "g"
        assert "account" not in state_change
    elif state_type == "local":
        assert state_change["app-state-type"] == "l"
        assert "account" in state_change
        # TODO: verify account is an algorand address
    elif state_type == "box":
        assert state_change["app-state-type"] == "b"
        assert "account" not in state_change
    else:
        raise Exception(f"Unknown state type: {state_type}")

    assert state_change["operation"] == "w"
    assert state_change["key"] == base64.b64encode(state_key.encode()).decode()
    assert "new-value" in state_change
    compare_avm_value_with_string_literal(
        state_new_value, state_change["new-value"]
    )


@then(
    '"{trace_type}" hash at txn-groups path "{txn_group_path}" should be "{b64_hash}".'
)
def program_hash_at_path_should_be(
    context, trace_type, txn_group_path, b64_hash
):
    assert context.atomic_transaction_composer_return
    assert context.atomic_transaction_composer_return.simulate_response
    simulation_response = (
        context.atomic_transaction_composer_return.simulate_response
    )

    txn_group_path_split = [
        int(p) for p in txn_group_path.split(",") if p != ""
    ]
    assert len(txn_group_path_split) > 0

    traces = simulation_response["txn-groups"][0]["txn-results"][
        txn_group_path_split[0]
    ]["exec-trace"]
    assert traces

    for p in txn_group_path_split[1:]:
        traces = traces["inner-trace"][p]
        assert traces

    hash = None
    if trace_type == "approval":
        hash = traces["approval-program-hash"]
    elif trace_type == "clearState":
        hash = traces["clear-state-program-hash"]
    elif trace_type == "logic":
        hash = traces["logic-sig-hash"]
    else:
        raise Exception(f"Unknown trace type: {trace_type}")

    assert hash == b64_hash


@when("we make a SetSyncRound call against round {round}")
def set_sync_round_call(context, round):
    context.response = context.acl.set_sync_round(round)


@when("we make a GetSyncRound call")
def get_sync_round_call(context):
    context.response = context.acl.get_sync_round()


@when("we make a UnsetSyncRound call")
def unset_sync_round_call(context):
    context.response = context.acl.unset_sync_round()


@when("we make a Ready call")
def ready_call(context):
    context.response = context.acl.ready()


@when("we make a SetBlockTimeStampOffset call against offset {offset}")
def set_block_timestamp_offset(context, offset):
    context.response = context.acl.set_timestamp_offset(offset)


@when("we make a GetBlockTimeStampOffset call")
def get_block_timestamp_offset(context):
    context.response = context.acl.get_timestamp_offset()


@when("we make a GetLedgerStateDelta call against round {round}")
def get_ledger_state_delta_call(context, round):
    context.response = context.acl.get_ledger_state_delta(
        round, response_format="msgpack"
    )


@when(
    "we make a TransactionGroupLedgerStateDeltaForRoundResponse call for round {round}"
)
def get_transaction_group_ledger_state_deltas_for_round(context, round):
    context.response = (
        context.acl.get_transaction_group_ledger_state_deltas_for_round(
            round, response_format="msgpack"
        )
    )


@when(
    'we make a LedgerStateDeltaForTransactionGroupResponse call for ID "{id}"'
)
def get_ledger_state_delta_for_transaction_group(context, id):
    context.response = (
        context.acl.get_ledger_state_delta_for_transaction_group(
            id, response_format="msgpack"
        )
    )


@when("we make a GetBlockTxids call against block number {round}")
def get_block_txids_call(context, round):
    context.response = context.acl.get_block_txids(round)
