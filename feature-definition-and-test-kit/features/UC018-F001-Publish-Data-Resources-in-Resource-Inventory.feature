# This feature corresponds to use case UC018 - Knowledge and Ontology Management
# (usecase-library/UC018-Knowledge-and-Ontology-Management.md) and covers the scenario where
# a Knowledge Management component is deployed and the Canvas publishes the data resources it
# provides in the Resource Inventory, so that users, agents and other components can discover them.
#
# The 'business stakeholders' for the canvas Behaviour Driven Design are the Engineering teams
# from the Vendor of a component, from a Systems Integrator who may be integrating and deploying
# a component, or a Service Provider's Operations team who may be supporting a component.
#
# The component declares its data resources in a provider DataResourceConfig. The Resource
# Inventory projects them as 'DataResource' resources (ADR-011).
#
# @SkipTest: these scenarios need the DataResourceConfig CRD and the Resource Inventory
# projection of data resources. Remove the tag when they are delivered
# (hw-irc-sni/oda-canvas#60 and #65).

@UC018 @UC018-F001
Feature: UC018-F001 Publish Data Resources in Resource Inventory
    As an ODA Canvas
    I want to publish the data resources that a component provides in the Resource Inventory
    So that users, agents and other components can discover them

    Background:
        Given only the 'knowledgemanagement-v1' package is installed
        When the 'ctk-knowledgemanagement' component has a deployment status of 'Complete'

    @UC018-F001-001 @SkipTest
    Scenario Outline: The data resources of the component are listed in the Resource Inventory
        Then the Resource Inventory API lists a 'DataResource' resource with 'type' '<type>' that is 'providedBy' the 'ctk-knowledgemanagement' resource

        Examples:
            | type           |
            | vectorStore    |
            | knowledgeGraph |

    @UC018-F001-002 @SkipTest
    Scenario: The component is identified and linked to its data resources
        Then the 'ctk-knowledgemanagement' resource in the Resource Inventory API has the characteristic 'componentId' with value 'TMFC060'
        And the 'ctk-knowledgemanagement' resource in the Resource Inventory API has a 'provides' relationship to 2 'DataResource' resources
