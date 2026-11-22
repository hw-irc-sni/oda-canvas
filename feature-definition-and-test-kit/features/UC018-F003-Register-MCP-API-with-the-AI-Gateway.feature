# This feature corresponds to use case UC018 - Knowledge and Ontology Management
# (usecase-library/UC018-Knowledge-and-Ontology-Management.md) and covers the scenario where
# a component that exposes an MCP API is deployed, and the Canvas registers that API with the
# AI gateway, so that agents can use its tools through the gateway without manual configuration.
#
# The 'business stakeholders' for the canvas Behaviour Driven Design are the Engineering teams
# from the Vendor of a component, from a Systems Integrator who may be integrating and deploying
# a component, or a Service Provider's Operations team who may be supporting a component.
#
# The Canvas registers only the MCP targets it owns, and removes them when the component is
# uninstalled. Targets added to the AI gateway by hand are left alone.
#
# @SkipTest: these scenarios need the automatic registration of MCP ExposedAPIs with the AI
# gateway, which follows the merge of the AI gateway work. Remove the tag when it is delivered
# (hw-irc-sni/oda-canvas#68).

@UC018 @UC018-F003
Feature: UC018-F003 Register MCP API with the AI Gateway
    As an ODA Canvas
    I want to register the MCP APIs that components expose with the AI gateway
    So that agents can discover and use their tools through the gateway

    Background:
        Given only the 'knowledgemanagement-v1' package is installed
        And the 'ctk-knowledgemanagement' component has a deployment status of 'Complete'

    @UC018-F003-001 @SkipTest
    Scenario: The MCP API of the component is registered with the AI gateway
        Then the AI gateway has an MCP target for the ExposedAPI 'ctk-knowledgemanagement-knowledgemcp' in namespace 'components'

    @UC018-F003-002 @SkipTest
    Scenario: The tools of the MCP API are listed through the AI gateway
        Then the tools of the MCP target for the ExposedAPI 'ctk-knowledgemanagement-knowledgemcp' in namespace 'components' can be listed through the AI gateway

    @UC018-F003-003 @SkipTest
    Scenario: The MCP target is removed when the component is uninstalled
        Given the AI gateway has an MCP target for the ExposedAPI 'ctk-knowledgemanagement-knowledgemcp' in namespace 'components'
        When I uninstall the release 'ctk'
        Then the AI gateway has no MCP target for the ExposedAPI 'ctk-knowledgemanagement-knowledgemcp' in namespace 'components'
