# This feature corresponds to use case UC018 - Knowledge and Ontology Management
# (usecase-library/UC018-Knowledge-and-Ontology-Management.md) and covers the scenario where
# a component declares that it consumes a knowledge-base served by a Knowledge Management
# component, and the Canvas binds it: the knowledge-base is ensured on the provider, the consumer
# is granted the provider's role, and the consumer receives a binding Secret and a DependentAPI.
#
# The 'business stakeholders' for the canvas Behaviour Driven Design are the Engineering teams
# from the Vendor of a component, from a Systems Integrator who may be integrating and deploying
# a component, or a Service Provider's Operations team who may be supporting a component.
#
# The consumer declares the dependency in a DataResourceConfig with role 'consumer' (ADR-010).
# The consumer never receives the credentials of the provider's stores; it calls the provider
# with its own credentials, so the role grant is what lets it query the knowledge-base.
#
# The scenarios need the Data Resource Management operator (TMFOP012), which canvas-oda installs only with
# dataresource-operator.enabled=true, and the TMFC060 images, which the knowledgemanagement-v1 test chart (a wrapper of
# the TMFC060 chart) runs. Both are built locally until they are published, so the scenarios keep @SkipTest in CI.
# They pass on a kind canvas installed with the operator enabled and the images loaded (hw-irc-sni/oda-canvas#64):
#   npm start -- --tags @UC018-F002

@UC018 @UC018-F002
Feature: UC018-F002 Bind a Consumer to a Knowledge Base
    As a component that needs knowledge
    I want the Canvas to bind me to a knowledge-base of a Knowledge Management component
    So that I can use it without handling the credentials of its stores

    Background:
        Given only the 'knowledgemanagement-v1' package is installed
        And the 'ctk-knowledgemanagement' component has a deployment status of 'Complete'
        And I install the 'productcatalog-v1' package as release 'kmc'
        And the 'kmc-productcatalogmanagement' component has a deployment status of 'Complete'

    @UC018-F002-001 @SkipTest
    Scenario: A consumer DataResourceConfig is bound to a knowledge-base
        When I create a consumer DataResourceConfig 'kmc-knowledge' for the 'kmc-productcatalogmanagement' component with the dependency 'incident-kb' on component 'TMFC060'
        Then the DataResourceConfig 'kmc-knowledge' has the condition 'Ready' with status 'True'
        And the Secret 'kmc-knowledge-incident-kb-binding' has the keys:
            | key      |
            | apiUrl   |
            | queryUrl |
            | mcpUrl   |
            | kbId     |
        And a DependentAPI owned by the DataResourceConfig 'kmc-knowledge' exists
        And the client 'kmc-productcatalogmanagement' has the role 'knowledge-viewer' of the client 'ctk-knowledgemanagement'

    @UC018-F002-002 @SkipTest
    Scenario: Deleting the consumer DataResourceConfig removes the binding
        Given I create a consumer DataResourceConfig 'kmc-knowledge' for the 'kmc-productcatalogmanagement' component with the dependency 'incident-kb' on component 'TMFC060'
        And the DataResourceConfig 'kmc-knowledge' has the condition 'Ready' with status 'True'
        When I delete the DataResourceConfig 'kmc-knowledge'
        Then the Secret 'kmc-knowledge-incident-kb-binding' does not exist
        And the client 'kmc-productcatalogmanagement' does not have the role 'knowledge-viewer' of the client 'ctk-knowledgemanagement'
