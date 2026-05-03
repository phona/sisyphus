.PHONY: test test-no-business-leak

test: ## run all tests
	go test ./...

test-no-business-leak: ## fail if any business name leaks into testkit source
	@! grep -rE "ttpos|shop_|cashier|company_uuid|staff_uuid|TTPOS" $$(find . -type f -name "*.go" -not -path "*/vendor/*") && \
		echo "test-no-business-leak: OK" || \
		(echo "test-no-business-leak: FAILED — business name found in testkit source" && exit 1)
