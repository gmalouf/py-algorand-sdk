from typing import Union

from algosdk import account, encoding, logic, transaction
from behave import given, then, when
import tests.steps.other_v2_steps  # Imports MaybeString


def fund_account_address(
    context, account_address: str, amount: Union[int, str]
):
    sp = context.app_acl.suggested_params()
    payment = transaction.PaymentTxn(
        context.accounts[0],
        sp,
        account_address,
        int(amount),
    )
    signed_payment = context.wallet.sign_transaction(payment)
    context.app_acl.send_transaction(signed_payment)
    transaction.wait_for_confirmation(context.app_acl, payment.get_txid(), 1)


@when(
    'we make an Account Information call against account "{account}" with exclude "{exclude:MaybeString}"'
)
def acc_info(context, account, exclude):
    context.response = context.acl.account_info(account, exclude=exclude)


@when('we make an Account Information call against account "{account}"')
def acc_info2(context, account):
    context.response = context.acl.account_info(account)


@when(
    'we make a Lookup Account by ID call against account "{account}" with round {block}'
)
def lookup_account(context, account, block):
    context.response = context.icl.account_info(account, int(block))


@when(
    'we make a Lookup Account by ID call against account "{account}" with exclude "{exclude:MaybeString}"'
)
def lookup_account2(context, account, exclude):
    context.response = context.icl.account_info(account, exclude=exclude)


@when("we make any LookupAccountByID call")
def lookup_account_any(context):
    context.response = context.icl.account_info(
        "PNWOET7LLOWMBMLE4KOCELCX6X3D3Q4H2Q4QJASYIEOF7YIPPQBG3YQ5YI", 12
    )


@then('the parsed LookupAccountByID response should have address "{address}"')
def parse_account(context, address):
    assert context.response["account"]["address"] == address


@when("we make any Account Information call")
def acc_info_any(context):
    context.response = context.acl.account_info(
        "PNWOET7LLOWMBMLE4KOCELCX6X3D3Q4H2Q4QJASYIEOF7YIPPQBG3YQ5YI"
    )


@then(
    'the parsed Account Information response should have address "{address}"'
)
def parse_acc_info(context, address):
    assert context.response["address"] == address


@when(
    'we make an Account Asset Information call against account "{account}" assetID {assetID}'
)
def acc_asset_info(context, account, assetID):
    context.response = context.acl.account_asset_info(account, assetID)


@when(
    'we make an Account Application Information call against account "{account}" applicationID {applicationID}'
)
def acc_application_info(context, account, applicationID):
    context.response = context.acl.account_application_info(
        account, applicationID
    )


@when(
    'we make a LookupAccountAssets call with accountID "{account}" assetID {asset_id} includeAll "{includeAll:MaybeBool}" limit {limit} next "{next:MaybeString}"'
)
def lookup_account_assets(context, account, asset_id, includeAll, limit, next):
    context.response = context.icl.lookup_account_assets(
        account,
        asset_id=int(asset_id),
        include_all=includeAll,
        limit=int(limit),
        next_page=next,
    )


@when(
    'we make a LookupAccountCreatedAssets call with accountID "{account}" assetID {asset_id} includeAll "{includeAll:MaybeBool}" limit {limit} next "{next:MaybeString}"'
)
def lookup_account_created_assets(
    context, account, asset_id, includeAll, limit, next
):
    context.response = context.icl.lookup_account_asset_by_creator(
        account,
        asset_id=int(asset_id),
        include_all=includeAll,
        limit=int(limit),
        next_page=next,
    )


@when(
    'we make a LookupAccountAppLocalStates call with accountID "{account}" applicationID {application_id} includeAll "{includeAll:MaybeBool}" limit {limit} next "{next:MaybeString}"'
)
def lookup_account_applications(
    context, account, application_id, includeAll, limit, next
):
    context.response = context.icl.lookup_account_application_local_state(
        account,
        application_id=int(application_id),
        include_all=includeAll,
        limit=int(limit),
        next_page=next,
    )


@when(
    'we make a LookupAccountCreatedApplications call with accountID "{account}" applicationID {application_id} includeAll "{includeAll:MaybeBool}" limit {limit} next "{next:MaybeString}"'
)
def lookup_account_created_applications(
    context, account, application_id, includeAll, limit, next
):
    context.response = context.icl.lookup_account_application_by_creator(
        account,
        application_id=int(application_id),
        include_all=includeAll,
        limit=int(limit),
        next_page=next,
    )


@then(
    'the parsed LookupAssetBalances response should be valid on round {roundNum}, and contain an array of len {length} and element number {idx} should have address "{address}" amount {amount} and frozen state "{frozenState}"'
)
def parse_asset_balance(
    context, roundNum, length, idx, address, amount, frozenState
):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["balances"]) == int(length)
    assert context.response["balances"][int(idx)]["address"] == address
    assert context.response["balances"][int(idx)]["amount"] == int(amount)
    assert context.response["balances"][int(idx)]["is-frozen"] == (
        frozenState == "true"
    )


@when(
    "we make a Search Accounts call with assetID {index} limit {limit} currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} and round {block}"
)
def search_accounts(
    context, index, limit, currencyGreaterThan, currencyLessThan, block
):
    context.response = context.icl.accounts(
        asset_id=int(index),
        limit=int(limit),
        next_page=None,
        min_balance=int(currencyGreaterThan),
        max_balance=int(currencyLessThan),
        block=int(block),
    )


@when(
    'we make a Search Accounts call with onlineOnly "{onlineOnly:MaybeBool}"'
)
def search_accounts_online_only(context, onlineOnly):
    context.response = context.icl.accounts(
        online_only=onlineOnly,
    )


@when(
    'we make a Search Accounts call with assetID {index} limit {limit} currencyGreaterThan {currencyGreaterThan} currencyLessThan {currencyLessThan} round {block} and authenticating address "{authAddr:MaybeString}"'
)
def search_accounts2(
    context,
    index,
    limit,
    currencyGreaterThan,
    currencyLessThan,
    block,
    authAddr,
):
    if authAddr == "none":
        authAddr = None
    context.response = context.icl.accounts(
        asset_id=int(index),
        limit=int(limit),
        next_page=None,
        min_balance=int(currencyGreaterThan),
        max_balance=int(currencyLessThan),
        block=int(block),
        auth_addr=authAddr,
    )


@when('we make a Search Accounts call with exclude "{exclude:MaybeString}"')
def search_accounts3(
    context,
    exclude,
):
    context.response = context.icl.accounts(exclude=exclude)


@when("we make any SearchAccounts call")
def search_accounts_any(context):
    context.response = context.icl.accounts(asset_id=2)


@then(
    'the parsed SearchAccounts response should be valid on round {roundNum} and the array should be of len {length} and the element at index {index} should have address "{address}"'
)
def parse_accounts(context, roundNum, length, index, address):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["accounts"]) == int(length)
    if int(length) > 0:
        assert context.response["accounts"][int(index)]["address"] == address


@when(
    'the parsed SearchAccounts response should be valid on round {roundNum} and the array should be of len {length} and the element at index {index} should have authorizing address "{authAddr:MaybeString}"'
)
def parse_accounts_auth(context, roundNum, length, index, authAddr):
    assert context.response["current-round"] == int(roundNum)
    assert len(context.response["accounts"]) == int(length)
    if int(length) > 0:
        assert (
            context.response["accounts"][int(index)]["auth-addr"] == authAddr
        )


@given('a signing account with address "{address}" and mnemonic "{mnemonic}"')
def signing_account(context, address, mnemonic):
    context.signing_address = address
    context.signing_mnemonic = mnemonic


@given(
    "I create a new transient account and fund it with {transient_fund_amount} microalgos."
)
def create_transient_and_fund(context, transient_fund_amount):
    context.transient_sk, context.transient_pk = account.generate_account()
    sp = context.app_acl.suggested_params()
    payment = transaction.PaymentTxn(
        context.accounts[0],
        sp,
        context.transient_pk,
        int(transient_fund_amount),
    )
    signed_payment = context.wallet.sign_transaction(payment)
    context.app_acl.send_transaction(signed_payment)
    transaction.wait_for_confirmation(context.app_acl, payment.get_txid(), 1)


@then(
    "I get the account address for the current application and see that it matches the app id's hash"
)
def assert_app_account_is_the_hash(context):
    app_id = context.current_application_id
    expected = encoding.encode_address(
        encoding.checksum(b"appID" + app_id.to_bytes(8, "big"))
    )
    actual = logic.get_application_address(app_id)
    assert (
        expected == actual
    ), f"account-address: expected [{expected}], but got [{actual}]"


@given(
    "I fund the current application's address with {fund_amount} microalgos."
)
def fund_app_account(context, fund_amount):
    fund_account_address(
        context,
        logic.get_application_address(context.current_application_id),
        fund_amount,
    )
